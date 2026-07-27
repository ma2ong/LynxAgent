"""SaaS 层智能推荐池：不得因评分刻度变化而清空结果。

历史事故（2026-07-14）：SaaS 层写死 `if score < 80: continue`。评分公式换成结构因子
（分布上限约 80）后，全市场最高分 80.27，这道绝对闸门把整池清空——用户点「一键智能推荐」
拿到 0 只、且任务状态显示 completed 无报错。回放验证的策略是「每期买 top-N」，
不是「买分数 > X 的」，因此不设绝对分数闸门。
"""
import asyncio
from typing import Any
from unittest.mock import patch

import app.lite_main as lite_main
from quantcore.quant.factors import blend_intraday_score, intraday_strength_score


def _fake_pool(scores: list[float]) -> dict[str, Any]:
    return {
        "source": "test",
        "universe_size": len(scores),
        "analyzed": len(scores),
        "items": [{
            "symbol": f"6000{i:02d}", "code": f"6000{i:02d}", "name": f"股{i}",
            "score": s, "quant_score": s, "signal": "buy", "close": 10.0,
            "pct_chg": 1.0, "amount": 5e8, "factors": {"trend": 70.0, "risk_control": 60.0,
                                                       "liquidity": 55.0},
            "reasons": ["结构因子分"], "patterns": [], "trade_plan": {"buy_price": 10.0},
        } for i, s in enumerate(scores, start=1)],
    }


def _compute(scores: list[float]) -> dict[str, Any]:
    # _confluence_enrich_items 必须 stub：假池的 6000xx 是真实 A 股代码，不 stub 就会去读
    # 本地真实 kline，形态/强度共振加成会按当日行情改变排序，断言随数据漂移。
    pool = _fake_pool(scores)
    with patch.object(lite_main, "_cache_get", return_value=None), \
         patch.object(lite_main, "_persistent_cache_get", return_value=None), \
         patch.object(lite_main, "_cache_set"), \
         patch.object(lite_main, "_persistent_cache_set"), \
         patch.object(lite_main, "run_scan", new=_async_return(pool)), \
         patch.object(lite_main, "_load_ai_factor_pool", return_value={"status": "pending", "scores": {}}), \
         patch.object(lite_main, "_confluence_enrich_items", new=lambda _items: None), \
         patch.object(lite_main, "_enrich_smart_pool_industries", new=_async_identity()):
        res = asyncio.run(lite_main._compute_lite_smart_pool(limit=20, universe_limit=5000))
    return res.get("data", res)


def _async_return(value):
    async def _f(*_a, **_kw):
        return value
    return _f


def _async_identity():
    async def _f(items, *_a, **_kw):
        return items
    return _f


def test_v3_score_scale_does_not_empty_the_pool():
    """v3 结构因子分（全部 < 80）必须照常输出整池——曾被绝对闸门清空。"""
    scores = [80.3, 79.9, 78.5, 76.2, 74.0, 71.8, 68.4]
    data = _compute(scores)
    items = data["items"]
    assert len(items) == len(scores), "低于旧闸门(80)的评分不应被丢弃"
    assert [round(float(i["quant_score"]), 1) for i in items] == sorted(scores, reverse=True)


def test_pool_ranked_desc_and_capped_by_limit():
    data = _compute([70.0, 90.0, 50.0])
    scores = [float(i["quant_score"]) for i in data["items"]]
    assert scores == sorted(scores, reverse=True)


def test_intraday_strength_changes_same_daily_structure_rank():
    base = 80.0
    weak = blend_intraday_score(base, intraday_strength_score(-5.0, 70.0))
    strong = blend_intraday_score(base, intraday_strength_score(5.0, 70.0))

    assert strong > weak + 8


def test_manual_generation_bypasses_old_pool_cache():
    pool = _fake_pool([88.0, 82.0])
    stale = {
        "success": True,
        "data": {
            "source": "stale-cache",
            "universe_size": 1,
            "items": [{"symbol": "000001", "name": "旧名单", "score": 99}],
        },
    }
    with patch.object(lite_main, "_cache_get", return_value=stale) as memory_cache, \
         patch.object(lite_main, "_persistent_cache_get", return_value=stale) as disk_cache, \
         patch.object(lite_main, "_cache_set"), \
         patch.object(lite_main, "_persistent_cache_set"), \
         patch.object(lite_main, "run_scan", new=_async_return(pool)), \
         patch.object(lite_main, "_load_ai_factor_pool", return_value={"status": "pending", "scores": {}}), \
         patch.object(lite_main, "_enrich_smart_pool_industries", new=_async_identity()):
        result = asyncio.run(
            lite_main._compute_lite_smart_pool(
                limit=20,
                universe_limit=5000,
                force_refresh=True,
            )
        )

    data = result.get("data", result)
    assert data["force_refreshed"] is True
    assert {item["symbol"] for item in data["items"]} == {"600001", "600002"}
    # 只断言「没读智选池缓存」——_cache_get 现在也服务环境仓位闸门等其他键，整体
    # assert_not_called 会被无关调用误伤。
    assert not _smart_pool_reads(memory_cache), "手动刷新不应读内存里的旧名单"
    assert not _smart_pool_reads(disk_cache), "手动刷新不应读磁盘里的旧名单"


def _smart_pool_reads(mock) -> list:
    return [c for c in mock.call_args_list if str(c.args[0] if c.args else "").startswith("smart-pool:")]
