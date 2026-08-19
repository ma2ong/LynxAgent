"""BYOK：用户自带 LLM 密钥的增删查与连通性测试。

产品不收费，推理成本不由站长承担；用户想用 AI 功能就填自己的密钥，谁用谁付。
密钥加密落库（见 app/core/user_llm_keys），接口从不回传明文，只回末 4 位。
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.user_llm_keys import PROVIDERS, get_store
from app.lite_auth import get_current_lite_user

router = APIRouter(prefix="/api/ai-key", tags=["byok"])


class SaveKeyRequest(BaseModel):
    provider: str = "deepseek"
    api_key: str
    base_url: Optional[str] = None
    model: Optional[str] = None


def _resolve_endpoint(req: SaveKeyRequest) -> tuple[str, str]:
    preset = PROVIDERS.get(req.provider) or {}
    base_url = (req.base_url or preset.get("base_url") or "").strip()
    model = (req.model or preset.get("model") or "").strip()
    return base_url, model


@router.get("/providers")
async def list_providers(_: dict[str, Any] = Depends(get_current_lite_user)):
    """预置服务商，省得用户自己查 base_url。"""
    return {
        "success": True,
        "data": [{"key": k, **v} for k, v in PROVIDERS.items()],
        "message": "ok",
    }


@router.get("/me")
async def my_key(user: dict[str, Any] = Depends(get_current_lite_user)):
    """当前用户配了什么。只回末 4 位，不回明文。"""
    return {"success": True, "data": get_store().meta(user["id"]), "message": "ok"}


@router.post("/test")
async def test_key(req: SaveKeyRequest, _: dict[str, Any] = Depends(get_current_lite_user)):
    """先验后存：填错的密钥当场告诉用户，而不是等他去点功能才发现不灵。"""
    import asyncio

    from quantcore.quant import llm

    base_url, model = _resolve_endpoint(req)
    if not base_url or not model:
        return {"success": False, "data": None, "message": "请填写接口地址与模型名"}
    override = {"provider": req.provider, "base_url": base_url, "model": model, "api_key": req.api_key}
    reply = await asyncio.to_thread(
        llm.chat, "回复两个字：可用", max_tokens=16, temperature=0, override=override
    )
    if not reply:
        return {"success": False, "data": None,
                "message": "连接失败：密钥、接口地址或模型名有误，也可能是账户余额不足"}
    return {"success": True, "data": {"reply": reply[:40], "model": model}, "message": "连接正常"}


@router.post("/save")
async def save_key(req: SaveKeyRequest, user: dict[str, Any] = Depends(get_current_lite_user)):
    base_url, model = _resolve_endpoint(req)
    if not req.api_key.strip():
        return {"success": False, "data": None, "message": "请填写密钥"}
    if not base_url or not model:
        return {"success": False, "data": None, "message": "请填写接口地址与模型名"}
    get_store().save(user["id"], req.provider, base_url, model, req.api_key.strip())
    return {"success": True, "data": get_store().meta(user["id"]), "message": "已保存"}


@router.delete("/me")
async def delete_key(user: dict[str, Any] = Depends(get_current_lite_user)):
    get_store().delete(user["id"])
    return {"success": True, "data": None, "message": "已删除"}
