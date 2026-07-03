"""集合竞价板块：从全市场快照的 今开(open) vs 昨收(prev_close) 推导当日竞价情绪。

数据来源：复用后端已有的全市场实时快照（腾讯/东财/akshare 级联），其中 open=今日开盘价
（即 09:25 集合竞价撮合价），prev_close=昨收。高开% = (open-prev_close)/prev_close。

说明：
- 盘前 09:15-09:25 运行时反映实时竞价；盘后运行反映当日开盘竞价结果（open 已固定）。
- 量比/成交额在盘后为全日累计，非竞价时段量，仅作辅助参考；核心可靠信号是高开幅度。
- 不依赖东财盘前分时(stock_zh_a_hist_pre_min_em)，该接口在本环境不稳定。
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Dict, List


def _in_auction_window(now: datetime | None = None) -> bool:
    """是否处于集合竞价时段（交易日 09:15–09:26）。该时段快照的成交量额即真实竞价撮合量额。"""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    return time(9, 15) <= now.time() <= time(9, 26)


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


# 科技成长热门行业白名单（东财行业名 f100 的子串匹配）。只在这些行业里选竞价买入股，
# 把白酒/食品/银行/保险/能源/风电/纺织等"老登"行业整体排除——竞价开得再好也不入选。
# 用户诉求：买入候选务必落在近期热门科技板块（光模块/PCB/存储/芯片/半导体/机器人/算力/AI）。
HOT_TECH_INDUSTRY_KEYWORDS = (
    "半导体", "光学光电", "元件", "电子化学", "其他电子", "消费电子",
    "通信设备", "通信服务", "计算机", "软件", "IT服务", "互联网",
    "自动化设备", "机器人", "电机",
    "专用设备",  # 半导体设备龙头(北方华创/中微/拓荆/赛腾)的 EM 行业归在此，必须纳入
)


def _is_hot_tech(industry: str) -> bool:
    industry = industry or ""
    return any(kw in industry for kw in HOT_TECH_INDUSTRY_KEYWORDS)


def compute_call_auction(
    snapshot: Dict[str, dict],
    sectors_config: List[dict],
    *,
    buy_limit: int = 15,
    industry_map: Dict[str, str] | None = None,
    hot_industries: Dict[str, float] | None = None,
    exclude_symbols: set | None = None,
    open_min: float = 1.5,
    open_max_ratio: float = 0.6,
) -> Dict[str, object]:
    """把全市场快照算成：竞价情绪概览 + 竞价热门板块 + 竞价买入推荐。

    买入候选的行业门槛优先级：
    1. hot_industries（近段趋势动态热门板块，{行业:近N日涨幅%}）—— 不写死赛道，跟随轮动；
    2. 缺失时退回静态科技成长白名单 HOT_TECH_INDUSTRY_KEYWORDS（兜底）；
    3. 连 industry_map 都没有时不做行业过滤（保证功能可用）。
    """
    imap = industry_map or {}
    hot = hot_industries or {}
    use_dynamic = bool(hot)
    exclude_set = {str(s).zfill(6) for s in (exclude_symbols or set())}

    def _industry_ok(industry: str) -> bool:
        if use_dynamic:
            return industry in hot       # 动态：近段趋势居前的板块
        return _is_hot_tech(industry)    # 兜底：静态科技白名单
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
            "industry": imap.get(code, ""),
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

    # 高开幅度分布（始终精确，仅依赖 今开/昨收）
    def _bucket(r: dict) -> str:
        op = r["open_pct"]
        if op >= r["board_limit"] * 0.985:
            return "竞价涨停"
        if op >= 5:
            return "大幅高开"
        if op >= 2:
            return "高开"
        if op > 0.05:
            return "微高开"
        if op > -0.05:
            return "平开"
        if op > -3:
            return "低开"
        return "大幅低开"

    _order = ["竞价涨停", "大幅高开", "高开", "微高开", "平开", "低开", "大幅低开"]
    _counts: Dict[str, int] = {k: 0 for k in _order}
    for r in rows:
        _counts[_bucket(r)] += 1
    distribution = [{"label": k, "count": _counts[k]} for k in _order]

    # 时段口径：竞价时段(09:15-09:25)快照的成交额即真实竞价撮合额；盘后为全日累计，仅作参考。
    in_window = _in_auction_window()
    caliber = "竞价撮合" if in_window else "全日参考"
    total_amount_yi = round(sum(r["amount"] for r in rows) / 1e8, 1)

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
        "distribution": distribution,
        "total_amount_yi": total_amount_yi,
        "caliber": caliber,
        "is_auction_window": in_window,
    }

    # 竞价热门板块：动态口径——与买入候选同源，用"近段趋势热门板块"(按近 N 日涨幅排名)，
    # 再叠加今日竞价强度；缺动态数据时退回 SECTOR_LEADERS 策划赛道（兜底）。全页口径一致。
    hot_sectors: List[dict] = []
    if use_dynamic:
        rows_by_ind: Dict[str, List[dict]] = {}
        for r in rows:
            ind = r.get("industry") or ""
            if ind:
                rows_by_ind.setdefault(ind, []).append(r)
        for ind, trend in sorted(hot.items(), key=lambda kv: kv[1], reverse=True):
            members = rows_by_ind.get(ind, [])
            if not members:
                continue
            avg = round(sum(m["open_pct"] for m in members) / len(members), 2)
            hi_cnt = sum(1 for m in members if m["open_pct"] > 0.05)
            top = max(members, key=lambda m: m["open_pct"])
            hot_sectors.append({
                "key": ind,
                "name": ind,
                "trend_pct": trend,
                "avg_open_pct": avg,
                "high_count": hi_cnt,
                "member_count": len(members),
                "leader": {"code": top["code"], "name": top["name"], "open_pct": top["open_pct"]},
            })
    else:
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

    # 板块共振强度：每个热门板块里"高开抢筹(≥1%)"的家数。共振越强，说明资金在该热门方向
    # 集体抢筹，个股当日走强概率越高——这是"在最近热门板块里重点选股"的量化抓手。
    gating = bool(imap)
    hot_open_by_industry: Dict[str, int] = {}
    if gating:
        for r in rows:
            ind = r.get("industry") or ""
            if not r["is_st"] and _industry_ok(ind) and r["open_pct"] >= 1.0:
                hot_open_by_industry[ind] = hot_open_by_industry.get(ind, 0) + 1

    # 竞价买入推荐：① 只在"近段趋势热门板块"里选（动态跟随轮动；缺数据时退回科技白名单；
    #   白酒/银行等不在近期热门就排除，但哪天它们趋势起来也会自动入选）② 健康高开（下限 open_min%，
    #   上限按板块涨停限自适应=板限×open_max_ratio，避开一字板、又给 20% 板的科技股留足空间）
    #   ③ 非 ST、非业绩暴雷、价格不仙 ④ 评分叠加板块共振 + 板块近段涨幅。
    candidates = []
    for r in rows:
        if r["is_st"] or r["price"] < 3:
            continue
        if r["code"] in exclude_set:
            continue  # 业绩暴雷/基本面利空 —— 不是"好股票"，剔除
        open_max = r["board_limit"] * open_max_ratio  # 10%板→6, 20%板→12, 30%板→18
        if not (open_min <= r["open_pct"] <= open_max):
            continue
        industry = r.get("industry") or ""
        if gating and not _industry_ok(industry):
            continue  # 不在近段趋势热门板块 —— 竞价开得再好也不入选
        vr = r["volume_ratio"]
        vr_eff = vr if vr > 0 else 1.0
        resonance = hot_open_by_industry.get(industry, 0)
        trend = hot.get(industry)  # 该板块近 N 日涨幅%（动态门槛下可得）
        # 评分：高开强度 + 量比配合 + 板块共振 + 板块近段趋势强度
        score = round(
            min(r["open_pct"], 7.0) * 6
            + min(vr_eff, 4.0) * 8
            + min(resonance, 12) * 1.8
            + (min(max(trend, 0.0), 30.0) * 0.6 if trend is not None else 0.0),
            1,
        )
        if r["open_pct"] >= 5:
            grab = "强抢筹"
        elif r["open_pct"] >= 3:
            grab = "抢筹"
        else:
            grab = "温和高开"
        reasons = [f"竞价高开 +{r['open_pct']:.2f}%"]
        if industry:
            tag = industry
            if trend is not None:
                tag = f"{industry}·近段+{trend:.1f}%"
            if resonance >= 3:
                tag = f"{tag}·{resonance}股共振"
            reasons.append(tag)
        if vr_eff >= 1.5:
            reasons.append(f"量比 {vr_eff:.1f}")
        if r["amount"]:
            reasons.append(f"{caliber}成交额 {r['amount'] / 1e8:.2f}亿")
        candidates.append({
            "code": r["code"],
            "name": r["name"],
            "open_pct": r["open_pct"],
            "price": round(r["price"], 2),
            "volume_ratio": round(vr, 2) if vr else None,
            "amount": r["amount"],
            "grab": grab,
            "score": score,
            "industry": industry,
            "theme": industry,
            "industry_trend": trend,
            "resonance": resonance,
            "reasons": reasons,
        })
    candidates.sort(key=lambda c: (c["score"], c.get("resonance", 0)), reverse=True)

    # 强弱排序可见化：名次(1=最强) + 综合强度(40~100，随名次递减) + 推荐档位。
    top_candidates = candidates[:buy_limit]
    if top_candidates:
        scores = [c["score"] for c in top_candidates]
        smax, smin = max(scores), min(scores)
        span = (smax - smin) or 1.0
        for idx, c in enumerate(top_candidates):
            c["rank"] = idx + 1
            c["strength"] = round(40 + (c["score"] - smin) / span * 60)
            ratio = c["score"] / (smax or 1.0)
            c["tier"] = "最强推荐" if ratio >= 0.9 else ("强推荐" if ratio >= 0.78 else "推荐")

    # 留痕当日竞价候选（首次快照），供复盘页统计真实 T+N 胜率。
    if top_candidates:
        try:
            from .local_store import get_local_store
            get_local_store().record_picks(
                "auction",
                [{**c, "close": c.get("price"), "symbol": c.get("code")} for c in top_candidates],
            )
        except Exception:
            pass

    dynamic_hot = [
        {"name": name, "trend_pct": score}
        for name, score in sorted(hot.items(), key=lambda kv: kv[1], reverse=True)
    ] if use_dynamic else []
    return {
        "available": True,
        "overview": overview,
        "hot_sectors": hot_sectors[:8],
        "buy_candidates": top_candidates,
        "gated_by_hot_sector": gating,
        "gating_mode": "dynamic_trend" if use_dynamic else ("static_tech" if gating else "off"),
        "dynamic_hot_industries": dynamic_hot,
        "note": (
            ("买入候选只在『近段趋势热门板块』中筛选——按各行业最近数日平均涨幅动态排名，跟随市场轮动、不写死赛道，"
             "哪个方向(含白酒/银行)趋势起来都会自动纳入。"
             if use_dynamic else
             "买入候选在科技成长行业白名单中筛选（动态趋势数据暂不可用，用兜底白名单）。")
            + f"再叠加竞价健康高开(下限{open_min:g}%、上限按板块涨停×{open_max_ratio:g}自适应)、板块共振与板块近段涨幅打分。"
            + ("" if gating else "（行业数据暂不可用，本次未做行业过滤）")
            + "竞价高开=今开/昨收；成交额口径：竞价时段(09:15-09:25)为真实撮合额，盘后为全日累计(仅参考)。研究用途，不构成投资建议。"
        ),
    }
