"""quant 模块统一的 LLM 调用入口（薄封装现有 OpenAI 兼容客户端）。

设计目标：
- 个股研报(A 的 AI 观点)、多 Agent 流水线(B 的 critic / 题材 Agent) 共用同一个 chat 接口，
  避免各写一份 OpenAI 客户端。
- 复用仓库现有配置：backend_url / 模型 / provider 来自 get_config()，api_key 来自对应 env。
- 使用 chat.completions（而非 OpenAI 专属 responses API），兼容 dashscope/deepseek 等 OpenAI 兼容服务。
- 无密钥时不抛栈，available() 返回 False，调用方据此降级（不调 LLM 的纯规则路径）。
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional

# provider -> 环境变量名（与 config_manager._get_env_api_key 对齐）
_PROVIDER_ENV = {
    "dashscope": "DASHSCOPE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


def _load_config() -> Dict[str, object]:
    """读取仓库配置；失败则回退到默认 OpenAI 配置（不抛异常）。"""
    try:
        from tradingagents.dataflows.interface import get_config

        return dict(get_config() or {})
    except Exception:
        return {}


def _resolve_api_key(provider: str) -> str:
    env_name = _PROVIDER_ENV.get((provider or "openai").lower(), "OPENAI_API_KEY")
    return os.getenv(env_name, "") or os.getenv("OPENAI_API_KEY", "")


def _client_and_model(deep: bool = False):
    """构造 OpenAI 兼容客户端与模型名；缺密钥返回 (None, None)。"""
    cfg = _load_config()
    # 优先级：仓库配置 > 环境变量 > 默认（OpenAI）。让 LLM provider 可由 .env 直接配置。
    provider = str(cfg.get("llm_provider") or os.getenv("LLM_PROVIDER") or "openai")
    base_url = str(cfg.get("backend_url") or os.getenv("LLM_BASE_URL") or "https://api.openai.com/v1")
    model = str(
        cfg.get("deep_think_llm" if deep else "quick_think_llm")
        or os.getenv("LLM_DEEP_MODEL" if deep else "LLM_QUICK_MODEL")
        or os.getenv("LLM_MODEL")
        or "gpt-4o-mini"
    )
    api_key = _resolve_api_key(provider)
    if not api_key:
        return None, None
    try:
        from openai import OpenAI

        return OpenAI(base_url=base_url, api_key=api_key), model
    except Exception:
        return None, None


def available() -> bool:
    """是否具备调用 LLM 的条件（有客户端 + 密钥）。调用方据此决定是否降级。"""
    client, _ = _client_and_model()
    return client is not None


def chat(
    prompt: str,
    system: Optional[str] = None,
    *,
    deep: bool = False,
    temperature: float = 0.3,
    max_tokens: int = 2048,
    model: Optional[str] = None,
) -> str:
    """单轮对话，返回纯文本。无密钥/出错时返回空串（调用方降级处理）。"""
    client, default_model = _client_and_model(deep=deep)
    if client is None:
        return ""
    messages: List[Dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    try:
        resp = client.chat.completions.create(
            model=model or default_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=30,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


def chat_json(
    prompt: str,
    system: Optional[str] = None,
    *,
    deep: bool = False,
    temperature: float = 0.2,
    max_tokens: int = 2048,
) -> Optional[dict]:
    """要求模型输出 JSON，解析为 dict。失败返回 None。

    容错：模型常把 JSON 包在 ```json ... ``` 或夹带前后文字，这里用正则抠出首个 {...} / [...]。
    """
    sys = (system or "") + "\n你必须只输出合法 JSON，不要任何解释或 markdown 代码块标记。"
    raw = chat(prompt, sys.strip(), deep=deep, temperature=temperature, max_tokens=max_tokens)
    if not raw:
        return None
    return _extract_json(raw)


def _extract_json(text: str) -> Optional[dict]:
    text = text.strip()
    # 去掉 ```json ... ``` 围栏
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # 抠第一个对象/数组
    for pattern in (r"\{.*\}", r"\[.*\]"):
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                continue
    return None
