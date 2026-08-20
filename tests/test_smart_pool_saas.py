"""SaaS 层智能推荐池：不得因评分刻度变化而清空结果。

历史事故（2026-07-14）：SaaS 层写死 `if score < 80: continue`。评分公式换成结构因子
（分布上限约 80）后，全市场最高分 80.27，这道绝对闸门把整池清空——用户点「一键智能推荐」
拿到 0 只、且任务状态显示 completed 无报错。回放验证的策略是「每期买 top-N」，
不是「买分数 > X 的」，因此不设绝对分数闸门。
"""
import asyncio
import inspect
from typing import Any
from unittest.mock import patch

import pytest

import app.lite_main as lite_main
from quantcore.quant.factors import blend_intraday_score, intraday_strength_score


@pytest.fixture(autouse=True)
def _disable_score_floor(monkeypatch):
    """默认关掉 90 分入选门槛（SMART_POOL_SCORE_FLOOR）。

    本文件多数用例的假分数在 50~88，开着门槛会被整池滤空，考的就不是原来那条不变量了。
    门槛本身由 test_score_floor_* 覆盖。
    """
    monkeypatch.setattr(lite_main, "SMART_POOL_SCORE_FLOOR", 0.0)


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
    assert sorted(round(float(i["quant_score"]), 1) for i in items) == sorted(scores)


def test_pool_ranked_by_intraday_composite_without_overwriting_structure_score(monkeypatch):
    # 这条考的不是「名单必须凑满」，而是另一个不变量；本用例的假分数跨度很大，
    # 会被 2026-08-06 加的质量线（LYNX_SMART_QUALITY_GAP，弱市不凑数）截断。
    # 显式关掉它，隔离被测对象。质量线本身由 test_quality_gap_* 覆盖。
    monkeypatch.setenv("LYNX_SMART_QUALITY_GAP", "0")
    data = _compute([70.0, 90.0, 50.0])
    items = data["items"]
    live_scores = [float(item["realtime_rank_score"]) for item in items]
    assert live_scores == sorted(live_scores, reverse=True)
    assert sorted(float(item["quant_score"]) for item in items) == [50.0, 70.0, 90.0]


def test_intraday_strength_changes_same_daily_structure_rank():
    base = 80.0
    weak = blend_intraday_score(base, intraday_strength_score(-5.0, 70.0))
    strong = blend_intraday_score(base, intraday_strength_score(5.0, 70.0))

    assert strong > weak + 8


def test_intraday_rerank_promotes_strong_live_candidate_without_changing_structure_score():
    items = [
        {
            "symbol": "600001",
            "smart_score": 80.0,
            "quant_score": 80.0,
            "pct_chg": -2.0,
            "amount": 50_000_000,
            "volume_ratio": 0.7,
            "reasons": [],
        },
        {
            "symbol": "600002",
            "smart_score": 76.0,
            "quant_score": 76.0,
            "pct_chg": 6.0,
            "amount": 500_000_000,
            "volume_ratio": 2.2,
            "reasons": [],
        },
    ]

    lite_main._rerank_smart_pool_intraday(items)

    assert items[0]["symbol"] == "600002"
    assert items[0]["quant_score"] == 76.0
    assert items[0]["realtime_rank_score"] > items[1]["realtime_rank_score"]
    assert items[0]["reasons"][0].startswith("盘中动态分")


def test_partial_realtime_coverage_does_not_rank_missing_quotes_as_fresh():
    items = [
        {
            "symbol": "600001",
            "smart_score": 80.0,
            "pct_chg": 1.5,
            "amount": 300_000_000,
            "reasons": [],
        },
        {
            "symbol": "600002",
            "smart_score": 70.0,
            "pct_chg": 9.0,
            "amount": 2_000_000_000,
            "reasons": [],
        },
    ]

    lite_main._rerank_smart_pool_intraday(items, fresh_symbols={"600001"})

    fresh = next(item for item in items if item["symbol"] == "600001")
    stale = next(item for item in items if item["symbol"] == "600002")
    assert stale["intraday_strength_score"] is None
    # 未覆盖的票按盘中中性 50 混合，和已覆盖的票同刻度，不能靠"退回结构原分"占便宜。
    assert stale["realtime_rank_score"] == round(70.0 * 0.78 + 50.0 * 0.22, 2)
    assert stale["reasons"][0] == "实时行情未覆盖，盘中项按中性计入，主要看结构分"
    assert fresh["intraday_strength_score"] is not None
    assert items[0]["symbol"] == "600001"


def test_realtime_enrichment_reranks_full_structure_candidate_pool(monkeypatch):
    # 这条考的不是「名单必须凑满」，而是另一个不变量；本用例的假分数跨度很大，
    # 会被 2026-08-06 加的质量线（LYNX_SMART_QUALITY_GAP，弱市不凑数）截断。
    # 显式关掉它，隔离被测对象。质量线本身由 test_quality_gap_* 覆盖。
    monkeypatch.setenv("LYNX_SMART_QUALITY_GAP", "0")
    candidates = [
        {
            "symbol": f"6000{i:02d}",
            "code": f"6000{i:02d}",
            "name": f"候选{i}",
            "smart_score": 81.0 - i,
            "quant_score": 81.0 - i,
            "score": 81.0 - i,
            "pct_chg": -2.0,
            "amount": 50_000_000,
            "reasons": [],
        }
        for i in range(1, 12)
    ]
    promoted_symbol = candidates[-1]["symbol"]

    async def realtime_quotes(symbols, **_kwargs):
        return {
            symbol: {
                "price": 10.0,
                "change_percent": 7.0 if symbol == promoted_symbol else -2.0,
                "amount": 800_000_000 if symbol == promoted_symbol else 50_000_000,
                "volume_ratio": 2.4 if symbol == promoted_symbol else 0.7,
                "updated_at": "2026/07/30 10:30:00",
            }
            for symbol in symbols
        }

    async def no_intraday_overlay(_data):
        return None

    response = {
        "success": True,
        "data": {
            "requested_limit": 10,
            "daily_as_of": "2026-07-29",
            "items": candidates[:10],
            "structure_candidates": candidates,
        },
    }
    gate = {"state": "偏暖", "label": "可正常参与", "coefficient": 1.0, "note": ""}
    with patch.object(lite_main, "_cache_get", return_value=gate), \
         patch.object(lite_main, "_realtime_quotes", new=realtime_quotes), \
         patch.object(lite_main, "_apply_intraday_quality", new=no_intraday_overlay), \
         patch.object(lite_main, "_update_smart_pool_list_basis", new=lambda _data: None):
        result = asyncio.run(lite_main._enrich_smart_pool_realtime(response))

    data = result["data"]
    assert data["intraday_candidate_count"] == 11
    assert len(data["items"]) == 10
    assert promoted_symbol in {item["symbol"] for item in data["items"]}
    assert data["realtime_as_of"] == "2026/07/30 10:30:00"
    assert data["realtime_quote_count"] == 11
    assert data["realtime_quote_total"] == 11
    assert data["realtime_coverage"] == 1.0


def test_realtime_enrichment_marks_missing_quotes_and_clears_stale_timestamp():
    response = {
        "success": True,
        "data": {
            "requested_limit": 10,
            "daily_as_of": "2026-07-29",
            "realtime_as_of": "2026/07/29 14:30:00",
            "items": [
                {"symbol": "600001", "smart_score": 80.0, "score": 80.0, "pct_chg": 8.0, "amount": 9e8, "reasons": []},
                {"symbol": "600002", "smart_score": 78.0, "score": 78.0, "pct_chg": 6.0, "amount": 8e8, "reasons": []},
            ],
        },
    }

    async def no_quotes(_symbols, **_kwargs):
        return {}

    async def no_intraday_overlay(_data):
        return None

    gate = lite_main._env_position_gate(None)
    with patch.object(lite_main, "_cache_get", return_value=gate), \
         patch.object(lite_main, "_realtime_quotes", new=no_quotes), \
         patch.object(lite_main, "_apply_intraday_quality", new=no_intraday_overlay), \
         patch.object(lite_main, "_update_smart_pool_list_basis", new=lambda _data: None):
        result = asyncio.run(lite_main._enrich_smart_pool_realtime(response))

    data = result["data"]
    assert data["realtime_status"] == "unavailable"
    assert data["realtime_quote_count"] == 0
    assert data["realtime_quote_total"] == 2
    assert data["realtime_coverage"] == 0.0
    assert data["realtime_as_of"] is None
    assert data["price_source"] == "最近完整日K（实时行情不可用）"
    assert all(item["intraday_strength_score"] is None for item in data["items"])


def test_environment_gate_never_treats_missing_market_data_as_neutral():
    missing = lite_main._env_position_gate(None)
    cold = lite_main._env_position_gate({"state": "偏冷", "temp": 21})
    neutral = lite_main._env_position_gate({"state": "中性", "temp": 50})
    warm = lite_main._env_position_gate({"state": "偏暖", "temp": 76})

    assert missing["coefficient"] == 0.0
    assert missing["max_single_position_pct"] == 0
    assert "暂停入场" in missing["label"]
    assert [cold["max_single_position_pct"], neutral["max_single_position_pct"], warm["max_single_position_pct"]] == [3, 6, 10]


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


def test_one_click_task_defaults_to_reusing_daily_pool():
    parameter = inspect.signature(lite_main.start_lite_smart_pool_task).parameters["force_refresh"]
    assert parameter.default is False


def test_nonempty_daily_pool_is_reused_without_full_market_scan():
    cached = {
        "success": True,
        "data": {
            "strategy": "quant_center_smart_pool",
            "items": [{"symbol": "600001", "name": "缓存候选", "score": 88.0}],
        },
    }

    async def fail_scan(*_args, **_kwargs):
        raise AssertionError("当天已有推荐池时不应再次全市场扫描")

    with patch.object(lite_main, "_cache_get", return_value=cached), \
         patch.object(lite_main, "run_scan", new=fail_scan), \
         patch.object(lite_main, "_enrich_smart_pool_realtime", new=_async_passthrough()):
        result = asyncio.run(
            lite_main._compute_lite_smart_pool(limit=10, universe_limit=5000)
        )

    assert result["data"]["items"][0]["symbol"] == "600001"


def test_empty_failed_response_is_never_considered_reusable_cache():
    assert not lite_main._smart_pool_response_has_items(
        {"success": True, "data": {"strategy": "all_market_recommend", "items": []}}
    )


def test_intraday_entry_confirmation_promotes_lower_structure_candidate():
    data = {
        "items": [
            {"symbol": "600001", "name": "结构更高", "smart_score": 80.0, "score": 80.0, "pct_chg": 1.0},
            {"symbol": "600002", "name": "量价确认", "smart_score": 76.0, "score": 76.0, "pct_chg": 3.0},
        ]
    }
    overlay = {
        "status": "live",
        "is_current": True,
        "phase": "morning",
        "signals": {
            "600002": {
                "status": "entry",
                "score": 91.0,
                "actionable": True,
                "signal_mode": "live",
                "reasons": ["放量突破20日压力位"],
            }
        },
    }

    lite_main._merge_intraday_quality(data, overlay)
    lite_main._finalize_intraday_quality(data, 10)

    assert [item["symbol"] for item in data["items"]] == ["600002", "600001"]
    assert data["items"][0]["timing_actionable"] is True
    assert data["items"][0]["quality_score"] == 84.0
    assert data["timing_actionable_count"] == 1


def test_near_limit_stays_on_list_and_star_market_is_not_misclassified():
    """涨停/近板是强势证据，标注买入难度后照常上榜，不做剔除。

    688 的涨跌幅上限是 20%，涨 9% 离板还远——板别判错会把正常上涨误标成近板。
    """
    data = {
        "items": [
            {"symbol": "600001", "name": "主板近涨停", "smart_score": 82.0, "score": 82.0, "pct_chg": 9.0},
            {"symbol": "688001", "name": "科创正常上涨", "smart_score": 80.0, "score": 80.0, "pct_chg": 9.0},
        ]
    }
    overlay = {"status": "live", "is_current": True, "signals": {}}

    lite_main._merge_intraday_quality(data, overlay)
    lite_main._finalize_intraday_quality(data, 10)

    by_symbol = {item["symbol"]: item for item in data["items"]}
    assert set(by_symbol) == {"600001", "688001"}
    assert by_symbol["600001"]["timing_status"] == "hot_limit"
    assert by_symbol["600001"]["timing_adjustment"] == 0.0
    assert by_symbol["600001"]["timing_actionable"] is False
    assert by_symbol["688001"]["timing_status"] == "unconfirmed"
    assert data["timing_excluded_count"] == 0


def test_one_click_recommendations_are_hard_capped_at_pool_max(monkeypatch):
    # 这条考的不是「名单必须凑满」，而是另一个不变量；本用例的假分数跨度很大，
    # 会被 2026-08-06 加的质量线（LYNX_SMART_QUALITY_GAP，弱市不凑数）截断。
    # 显式关掉它，隔离被测对象。质量线本身由 test_quality_gap_* 覆盖。
    monkeypatch.setenv("LYNX_SMART_QUALITY_GAP", "0")
    data = {
        "items": [
            {
                "symbol": f"600{i:03d}",
                "name": f"候选{i}",
                "smart_score": 90.0 - i,
                "score": 90.0 - i,
                "pct_chg": 1.0,
            }
            for i in range(25)
        ]
    }

    lite_main._merge_intraday_quality(
        data,
        {"status": "waiting", "is_current": False, "signals": {}},
    )
    lite_main._finalize_intraday_quality(data, 30)

    assert len(data["items"]) == lite_main.SMART_POOL_MAX_ITEMS
    assert data["requested_limit"] == lite_main.SMART_POOL_MAX_ITEMS
    assert [item["rank"] for item in data["items"]] == list(
        range(1, lite_main.SMART_POOL_MAX_ITEMS + 1)
    )


def test_final_list_reports_industry_concentration_without_hiding_candidates(monkeypatch):
    # 这条考的不是「名单必须凑满」，而是另一个不变量；本用例的假分数跨度很大，
    # 会被 2026-08-06 加的质量线（LYNX_SMART_QUALITY_GAP，弱市不凑数）截断。
    # 显式关掉它，隔离被测对象。质量线本身由 test_quality_gap_* 覆盖。
    monkeypatch.setenv("LYNX_SMART_QUALITY_GAP", "0")
    data = {
        "items": [
            {
                "symbol": f"600{i:03d}",
                "name": f"候选{i}",
                "industry": "半导体" if i < 4 else f"行业{i}",
                "smart_score": 90.0 - i,
                "score": 90.0 - i,
                "pct_chg": 1.0,
            }
            for i in range(10)
        ]
    }

    lite_main._merge_intraday_quality(data, {"status": "waiting", "is_current": False, "signals": {}})
    lite_main._finalize_intraday_quality(data, 10)

    assert len(data["items"]) == 10
    assert data["industry_concentration"]["warning"] is True
    assert data["industry_concentration"]["top_industry"] == "半导体"
    assert data["industry_concentration"]["top_count"] == 4


def _smart_pool_reads(mock) -> list:
    return [c for c in mock.call_args_list if str(c.args[0] if c.args else "").startswith("smart-pool:")]


def _async_passthrough():
    async def _f(value, *_args, **_kwargs):
        return value
    return _f


def _pick(symbol: str, score: float) -> dict:
    return {"symbol": symbol, "code": symbol, "name": f"票{symbol}",
            "quality_score": score, "score": score, "timing_status": "confirmed"}


def test_quality_gap_drops_the_tail_instead_of_padding_to_the_limit(monkeypatch):
    """弱市宁可少给几只，也不为凑满上限硬塞。

    起因（2026-08-06）：当日综合排序 ≥90 的只有 4 只，第 7 名就掉到 83.7，而推荐上限
    是 20，于是尾部塞进一串 79~81 分的票。用户看到的是「你怎么把 80 分的也推给我」。
    """
    monkeypatch.setenv("LYNX_SMART_QUALITY_GAP", "8")
    kept = [_pick("60000%d" % i, s) for i, s in enumerate([92.0, 89.0, 85.0, 84.5, 83.0, 79.0])]
    survivors, note = lite_main._drop_far_below_best(kept)
    # 门槛 = 92 − 8 = 84，低于它的 83.0 / 79.0 落榜
    assert [s["quality_score"] for s in survivors] == [92.0, 89.0, 85.0, 84.5]
    assert "弱市不凑数" in note


def test_quality_gap_never_empties_the_list(monkeypatch):
    """无论分数多低、跨度多大，最高分那只必然留下 —— 相对门槛不会清空名单。"""
    monkeypatch.setenv("LYNX_SMART_QUALITY_GAP", "8")
    survivors, _ = lite_main._drop_far_below_best([_pick("600001", 40.0), _pick("600002", 10.0)])
    assert [s["quality_score"] for s in survivors] == [40.0]
    assert lite_main._drop_far_below_best([]) == ([], "")


def test_quality_gap_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("LYNX_SMART_QUALITY_GAP", "0")
    kept = [_pick("600001", 92.0), _pick("600002", 50.0)]
    survivors, note = lite_main._drop_far_below_best(kept)
    assert len(survivors) == 2 and note == ""


def test_strong_day_keeps_everything(monkeypatch):
    """分数密集的强势日不该被误伤：跨度小于门槛时一只都不剔。"""
    monkeypatch.setenv("LYNX_SMART_QUALITY_GAP", "8")
    kept = [_pick("60000%d" % i, s) for i, s in enumerate([95.0, 94.0, 92.5, 90.0, 88.5])]
    survivors, note = lite_main._drop_far_below_best(kept)
    assert len(survivors) == 5 and note == ""


def _floor_pick(symbol: str, live_score: float) -> dict:
    return {"symbol": symbol, "code": symbol, "name": f"票{symbol}",
            "smart_score": 80.0, "score": 80.0, "pct_chg": 3.0,
            "realtime_rank_score": live_score}


def _finalize_with_floor(items: list[dict], floor: float = 90.0) -> dict:
    data = {"items": items}
    lite_main._merge_intraday_quality(
        data, {"status": "waiting", "is_current": False, "signals": {}}
    )
    lite_main._finalize_intraday_quality(data, 20, score_floor=floor)
    return data


def test_score_floor_keeps_every_qualified_pick_without_count_cap(monkeypatch):
    """够 90 分的全给，不再按名次砍到 20 只（2026-08-20 Allen 定的口径）。"""
    monkeypatch.setenv("LYNX_SMART_QUALITY_GAP", "0")
    data = _finalize_with_floor(
        [_floor_pick(f"600{i:03d}", 99.0 - i * 0.2) for i in range(28)]
    )

    assert len(data["items"]) == 28 > lite_main.SMART_POOL_MAX_ITEMS
    assert [item["rank"] for item in data["items"]] == list(range(1, 29))
    assert data["score_floor"] == 90.0


def test_score_floor_drops_everything_below_the_line(monkeypatch):
    monkeypatch.setenv("LYNX_SMART_QUALITY_GAP", "0")
    data = _finalize_with_floor(
        [_floor_pick("600001", 93.5), _floor_pick("600002", 89.9), _floor_pick("600003", 70.0)]
    )

    assert [item["symbol"] for item in data["items"]] == ["600001"]
    assert not data["score_floor_note"]


def test_score_floor_falls_back_to_the_strongest_few_and_says_so(monkeypatch):
    """一只都不达标时给当日最强的几只，但必须标明未达标——空名单是死路。"""
    monkeypatch.setenv("LYNX_SMART_QUALITY_GAP", "0")
    monkeypatch.setattr(lite_main, "SMART_POOL_FLOOR_FALLBACK", 5)
    data = _finalize_with_floor([_floor_pick(f"600{i:03d}", 86.4 - i) for i in range(9)])

    assert [item["symbol"] for item in data["items"]] == [f"600{i:03d}" for i in range(5)]
    assert data["score_floor_fallback"] is True
    assert data["score_floor_best"] == 86.4
    assert "86.4" in data["score_floor_note"] and "未达标" in data["score_floor_note"]


def test_score_floor_fallback_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("LYNX_SMART_QUALITY_GAP", "0")
    monkeypatch.setattr(lite_main, "SMART_POOL_FLOOR_FALLBACK", 0)
    data = _finalize_with_floor([_floor_pick("600001", 86.4), _floor_pick("600002", 80.0)])

    assert data["items"] == []
    assert data["score_floor_fallback"] is False
    assert "86.4" in data["score_floor_note"] and "90" in data["score_floor_note"]


def test_score_floor_fallback_stays_out_of_the_way_when_someone_qualifies(monkeypatch):
    monkeypatch.setenv("LYNX_SMART_QUALITY_GAP", "0")
    monkeypatch.setattr(lite_main, "SMART_POOL_FLOOR_FALLBACK", 5)
    data = _finalize_with_floor([_floor_pick("600001", 93.0), _floor_pick("600002", 80.0)])

    assert [item["symbol"] for item in data["items"]] == ["600001"]
    assert data["score_floor_fallback"] is False
    assert not data["score_floor_note"]


def test_score_floor_zero_falls_back_to_the_old_rank_cap(monkeypatch):
    """回滚开关：LYNX_SMART_SCORE_FLOOR=0 回到旧的 20 名上限。"""
    monkeypatch.setenv("LYNX_SMART_QUALITY_GAP", "0")
    data = _finalize_with_floor(
        [_floor_pick(f"600{i:03d}", 99.0 - i * 0.2) for i in range(28)], floor=0.0
    )

    assert len(data["items"]) == lite_main.SMART_POOL_MAX_ITEMS
