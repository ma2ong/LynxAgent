"""所有 AI 功能已停用：即使配了密钥，大模型调用也必须一律不可用。"""
from quantcore.quant import llm


def test_llm_unavailable_even_with_keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert llm.available() is False
    assert llm.available(override={"api_key": "sk-user", "base_url": "https://x", "model": "m"}) is False
    assert llm.chat("hi") == ""
