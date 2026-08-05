"""智选盘中重排必须看得见「今天哪个板块在爆发」。

背景：结构分里的 industry_heat 因子读 `stage_inputs`，而它只取 amount>0 的真实 bar，
跳过盘中占位 bar —— 永远落后一个交易日。2026-08-05 当天半导体全市场 +4.55%，该因子
给它的百分位是 4.8（"截至昨天"最弱）；软件开发 96（"截至昨天"最强）。用户看到的
「总是慢半拍、老推之前涨得好的软件股」就是这么来的。

所以当日板块强弱只能在盘中重排这一层补。这里钉住三件事：加分方向、不带实时行情时
不生效、以及板块信息缺失时不能把重排搞崩。
"""
import app.lite_main as m


def _item(symbol: str, industry: str, score: float = 80.0, pct: float = 3.0) -> dict:
    return {
        "symbol": symbol, "code": symbol, "name": f"票{symbol}", "industry": industry,
        "smart_score": score, "score": score, "pct_chg": pct,
        "amount": 5e8, "volume_ratio": 1.5, "reasons": [],
    }


def _rank(items, theme_ranks, fresh=None):
    m._rerank_smart_pool_intraday(items, fresh_symbols=fresh, theme_ranks=theme_ranks)
    return {i["symbol"]: i for i in items}


def test_hot_sector_lifts_an_otherwise_identical_stock():
    """结构分、涨幅、量能完全相同，身处当日最强主题（存储芯片 99 分位）的必须排前面。"""
    items = [_item("000001", "软件开发"), _item("000002", "贵金属")]
    out = _rank(items, {"000001": (0.30, "软件开发"), "000002": (0.98, "存储芯片")})
    assert out["000002"]["realtime_rank_score"] > out["000001"]["realtime_rank_score"]
    assert out["000002"]["sector_bonus"] > 0 > out["000001"]["sector_bonus"]
    assert items[0]["symbol"] == "000002", "重排后热板块的票应排在首位"


def test_bonus_is_bounded_by_the_configured_cap():
    """分位 1.0 / 0.0 分别对应 +cap / −cap，不能超。"""
    items = [_item("000001", "最强"), _item("000002", "最弱")]
    out = _rank(items, {"000001": (1.0, "最强"), "000002": (0.0, "最弱")})
    assert abs(out["000001"]["sector_bonus"] - m.SMART_POOL_SECTOR_BONUS) < 1e-6
    assert abs(out["000002"]["sector_bonus"] + m.SMART_POOL_SECTOR_BONUS) < 1e-6


def test_no_bonus_without_a_fresh_quote():
    """没有当日行情就谈不上"当日板块"，此时必须按 0 计，不能凭昨天的数据加分。"""
    items = [_item("000001", "贵金属")]
    out = _rank(items, {"000001": (0.98, "存储芯片")}, fresh=set())  # 该票不在 fresh 集合里
    assert out["000001"]["sector_bonus"] == 0.0
    assert out["000001"]["theme_rank_percentile"] == 98.0  # 仍然透出，只是不计分


def test_missing_sector_data_does_not_break_reranking():
    """板块映射拿不到时（行业为空 / sector_ranks 为空）重排照常跑完。"""
    items = [_item("000001", ""), _item("000002", "贵金属")]
    out = _rank(items, {})
    assert all(i["sector_bonus"] == 0.0 for i in out.values())
    assert all(i["realtime_rank_score"] > 0 for i in out.values())
