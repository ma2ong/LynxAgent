"""集合竞价板块：从全市场快照的 今开(open) vs 昨收(prev_close) 推导当日竞价情绪。

数据来源：复用后端已有的全市场实时快照（腾讯/东财/akshare 级联），其中 open=今日开盘价
（即 09:25 集合竞价撮合价），prev_close=昨收。高开% = (open-prev_close)/prev_close。

说明：
- 盘前 09:15-09:25 运行时反映实时竞价；盘后运行反映当日开盘竞价结果（open 已固定）。
- 量比/成交额在盘后为全日累计，非竞价时段量，仅作辅助参考；核心可靠信号是高开幅度。
- 不依赖东财盘前分时(stock_zh_a_hist_pre_min_em)，该接口在本环境不稳定。
"""
from __future__ import annotations

from typing import Dict, List


def _board_limit(symbol: str) -> float:
    """日涨跌幅上限：科创/创业 20%，北交 30%，主板 10%。"""
    if symbol.startswith(("688", "689", "300", "301")):
        return 20.0
    if symbol.startswith(("8", "4", "920")):
        return 30.0
    return 10.0


def _num(value, default=0.0) -> float:
    try:
        if value in (None, "", "-"):
            return default
        f = float(value)
        return default if f != f else f
    except (TypeError, ValueError):
        return default


def compute_call_auction(
    snapshot: Dict[str, dict],
    sectors_config: List[dict],
    *,
    buy_limit: int = 15,
) -> Dict[str, object]:
    """把全市场快照算成：竞价情绪概览 + 竞价热门板块 + 竞价买入推荐。"""
    rows: List[dict] = []
    for v in snapshot.values():
        code = str(v.get("code") or v.get("symbol") or "").zfill(6)
        if len(code) != 6 or not code.isdigit():
            continue
        op = _num(v.get("open"))
        pc = _num(v.get("prev_close"))
        if op <= 0 or pc <= 0:
            continue
        name = str(v.get("name") or code)
        rows.append({
            "code": code,
            "name": name,
            "open_pct": round((op - pc) / pc * 100, 2),
            "price": _num(v.get("price")),
            "amount": _num(v.get("amount")),
            "volume_ratio": _num(v.get("volume_ratio")),
            "turnover_rate": _num(v.get("turnover_rate")),
            "total_mv": _num(v.get("total_mv")),
            "is_st": "ST" in name.upper() or "退" in name,
            "board_limit": _board_limit(code),
        })

    total = len(rows)
    if total == 0:
        return {"available": False, "note": "暂无快照数据（盘前 09:15-09:25 或交易时段更准）。"}

    high = [r for r in rows if r["open_pct"] > 0.05]
    low = [r for r in rows if r["open_pct"] < -0.05]
    flat = total - len(high) - len(low)
    big_high = [r for r in rows if r["open_pct"] >= 3]
    big_low = [r for r in rows if r["open_pct"] <= -3]
    limit_open = [r for r in rows if r["open_pct"] >= r["board_limit"] * 0.985]
    limit_down_open = [r for r in rows if r["open_pct"] <= -r["board_limit"] * 0.985]
    avg_open = round(sum(r["open_pct"] for r in rows) / total, 2)
    high_ratio = round(len(high) / total * 100, 1)

    # 竞价情绪分（0-100）：高开占比 + 平均高开幅度
    sentiment = max(0.0, min(100.0, 50 + (high_ratio - 50) * 1.2 + avg_open * 8))
    sentiment = round(sentiment, 1)
    if sentiment >= 68:
        mood, verdict = "强", "竞价整体高开抢筹，当日情绪偏积极，可重点跟踪强势高开方向。"
    elif sentiment >= 56:
        mood, verdict = "偏强", "高开家数占优，情绪温和向上，关注高开不破的延续品种。"
    elif sentiment >= 44:
        mood, verdict = "中性", "竞价多空分化，缺乏明确合力，宜等量价确认再动手。"
    elif sentiment >= 32:
        mood, verdict = "偏弱", "低开家数偏多，情绪谨慎，警惕高开回落与冲高乏力。"
    else:
        mood, verdict = "弱", "竞价普遍低开，当日情绪偏弱，控制仓位、以防守为主。"

    overview = {
        "total": total,
        "high_open": len(high),
        "low_open": len(low),
        "flat_open": flat,
        "high_ratio": high_ratio,
        "avg_open_pct": avg_open,
        "big_high_open": len(big_high),
        "big_low_open": len(big_low),
        "limit_up_open": len(limit_open),
        "limit_down_open": len(limit_down_open),
        "sentiment_score": sentiment,
        "mood": mood,
        "verdict": verdict,
    }

    # 竞价热门板块：按策划赛道，算成分股平均竞价高开 + 高开家数，排序
    hot_sectors: List[dict] = []
    row_by_code = {r["code"]: r for r in rows}
    for sector in sectors_config:
        members = []
        for code, _name in sector["leaders"]:
            r = row_by_code.get(str(code).zfill(6))
            if r:
                members.append(r)
        if not members:
            continue
        avg = round(sum(m["open_pct"] for m in members) / len(members), 2)
        hi_cnt = sum(1 for m in members if m["open_pct"] > 0.05)
        top = max(members, key=lambda m: m["open_pct"])
        hot_sectors.append({
            "key": sector["key"],
            "name": sector["name"],
            "avg_open_pct": avg,
            "high_count": hi_cnt,
            "member_count": len(members),
            "leader": {"code": top["code"], "name": top["name"], "open_pct": top["open_pct"]},
        })
    hot_sectors.sort(key=lambda s: (s["avg_open_pct"], s["high_count"]), reverse=True)

    # 竞价买入推荐：健康高开(1.5%~7%，非一字板)、非 ST、有量、价格不仙
    candidates = []
    for r in rows:
        if r["is_st"] or r["price"] < 3:
            continue
        if not (1.5 <= r["open_pct"] <= 7.0):
            continue
        vr = r["volume_ratio"]
        # 评分：高开强度 + 量比配合（量比缺失给中性 1.0）
        vr_eff = vr if vr > 0 else 1.0
        score = round(min(r["open_pct"], 7.0) * 6 + min(vr_eff, 4.0) * 8, 1)
        reasons = [f"竞价高开 +{r['open_pct']:.2f}%"]
        if vr_eff >= 1.5:
            reasons.append(f"量比 {vr_eff:.1f}")
        if r["amount"]:
            reasons.append(f"成交额 {r['amount'] / 1e8:.2f}亿")
        candidates.append({
            "code": r["code"],
            "name": r["name"],
            "open_pct": r["open_pct"],
            "price": round(r["price"], 2),
            "volume_ratio": round(vr, 2) if vr else None,
            "amount": r["amount"],
            "score": score,
            "reasons": reasons,
        })
    candidates.sort(key=lambda c: c["score"], reverse=True)

    return {
        "available": True,
        "overview": overview,
        "hot_sectors": hot_sectors[:8],
        "buy_candidates": candidates[:buy_limit],
        "note": "竞价高开=今开/昨收。盘前 09:15-09:25 反映实时竞价，盘后反映当日开盘结果；量比/成交额为辅助参考。研究用途，不构成投资建议。",
    }
