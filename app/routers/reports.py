"""研报列表/搜索路由（从 lite_main 拆出）。

list 是未接 MongoDB 时的空 stub；search 走本地 FTS 表（app/core/analysis_store）。
路径不变（无 prefix）。
"""
from __future__ import annotations

from fastapi import APIRouter

from app.core.analysis_store import _search_reports_fts

router = APIRouter(tags=["reports"])


@router.get("/api/reports/list")
async def reports_list(page: int = 1, page_size: int = 20):
    return {
        "success": True,
        "data": {"reports": [], "total": 0, "page": page, "page_size": page_size},
        "message": "SaaS Lite 未连接 MongoDB，报告列表为空",
    }


@router.get("/api/reports/search")
async def reports_search(q: str = "", limit: int = 20):
    if not q.strip():
        return {"success": False, "data": [], "message": "请输入搜索关键词"}
    results = _search_reports_fts(q.strip(), limit)
    return {"success": True, "data": results, "message": f"共 {len(results)} 条结果"}
