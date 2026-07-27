"""SaaS Lite 静态配置路由（模型/默认设置）。

从 lite_main 拆出：这些 handler 只返回固定配置字典，零依赖、无鉴权，直接独立成
router。路径保持不变（无 prefix），前端无感。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter(tags=["config"])


@router.get("/api/config/settings")
async def config_settings():
    return {
        "success": True,
        "data": {
            "quick_analysis_model": "qwen-turbo",
            "deep_analysis_model": "qwen-max",
            "default_market": "A股",
            "default_depth": 3,
            "runtime_mode": "saas-lite",
        },
        "message": "SaaS Lite 默认配置",
    }


@router.put("/api/config/settings")
async def update_config_settings(settings: dict[str, Any]):
    return {"success": True, "data": {"message": "SaaS Lite 已接收配置", "settings": settings}, "message": "ok"}


@router.get("/api/config/llm")
async def config_llm():
    return {
        "success": True,
        "data": [
            {
                "provider": "qwen",
                "model_name": "qwen-turbo",
                "model_display_name": "qwen-turbo",
                "max_tokens": 4096,
                "temperature": 0.7,
                "timeout": 60,
                "retry_times": 2,
                "enabled": True,
                "description": "SaaS Lite 默认快速分析模型",
                "capability_level": 2,
                "suitable_roles": ["quick_analysis", "both"],
                "features": ["fast_response", "cost_effective"],
                "recommended_depths": ["快速", "基础", "标准"],
                "performance_metrics": {"speed": 5, "cost": 5, "quality": 3},
            },
            {
                "provider": "qwen",
                "model_name": "qwen-max",
                "model_display_name": "qwen-max",
                "max_tokens": 8192,
                "temperature": 0.5,
                "timeout": 120,
                "retry_times": 2,
                "enabled": True,
                "description": "SaaS Lite 默认深度分析模型",
                "capability_level": 4,
                "suitable_roles": ["deep_analysis", "both"],
                "features": ["reasoning", "long_context"],
                "recommended_depths": ["标准", "深度", "全面"],
                "performance_metrics": {"speed": 3, "cost": 3, "quality": 4},
            },
        ],
        "message": "SaaS Lite 默认模型列表",
    }


@router.post("/api/model-capabilities/recommend")
async def recommend_models(payload: dict[str, Any]):
    depth = payload.get("research_depth") or "标准"
    return {
        "success": True,
        "data": {
            "research_depth": depth,
            "quick_analysis_model": "qwen-turbo",
            "deep_analysis_model": "qwen-max",
            "recommendations": {
                "quick_model": "qwen-turbo",
                "deep_model": "qwen-max",
            },
            "reason": "SaaS Lite 使用本地默认模型配置，重点保证页面流程可运行。",
        },
        "message": "ok",
    }
