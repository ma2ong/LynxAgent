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


# 推荐档位门槛：候选评分占当日最高分的比例。同时决定展示范围——达到强推荐档就上榜。
TOP_TIER_RATIO = 0.9
STRONG_TIER_RATIO = 0.78


def _is_hot_tech(industry: str) -> bool:
    industry = industry or ""
    return any(kw in industry for kw in HOT_TECH_INDUSTRY_KEYWORDS)


def compute_call_auction(
    snapshot: Dict[str, dict],
    sectors_config: List[dict],
    *,
    buy_limit: int = 15,
    # 展示口径按「档位」而不是按名次：凡是评分达到当日最高分 78%（即『强推荐』及以上）
    # 的候选全部上榜，多少只给多少只。旧口径固定砍到前 3 名，强弱本来相近的第 4、5 名
    # 被无差别丢掉；档位口径让"今天到底有几只够强"由盘面自己决定。
    # 留痕仍按 buy_limit 记满，否则以后无法继续验证名次与涨停率的关系。
    min_display: int = 3,
    industry_map: Dict[str, str] | None = None,
    hot_industries: Dict[str, float] | None = None,
    exclude_symbols: set | None = None,
    open_min: float = 1.5,
    open_max_ratio: float = 0.6,
    record: bool = True,
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
        # 板块趋势只做加权，不再一票否决。
        # 回测（12 个月 / 3610 笔 / 买开盘卖 T+1 收盘）：只追健康高开的基线是 +0.438pp、
        # 胜率 52.4%、t=2.57；一旦加上「只在近段强势板块里选」的硬闸门，降到 +0.296pp、
        # 胜率 48.3%、t=1.45，近两月更是掉到 41.5%——和线上留痕实测的 42% 吻合。
        # 道理也直白：近段强势板块就是刚涨完的板块，硬闸门等于把资金定向送进最接近
        # 见顶的方向。趋势分继续留在评分里（下面的 trend 项），让它影响排序而不是准入。
        in_hot = (not gating) or _industry_ok(industry)
        vr = r["volume_ratio"]
        vr_eff = vr if vr > 0 else 1.0
        resonance = hot_open_by_industry.get(industry, 0)
        trend = hot.get(industry)  # 该板块近 N 日涨幅%（动态门槛下可得）
        # 评分：高开强度 + 量比配合 + 板块共振 + 板块近段趋势强度
        score = round(
            min(r["open_pct"], 7.0) * 6
            + min(vr_eff, 4.0) * 8
            + min(resonance, 12) * 1.8
            + (min(max(trend, 0.0), 30.0) * 0.6 if trend is not None else 0.0)
            + (6.0 if in_hot else 0.0),   # 属于热门板块给固定加分，替代原来的硬闸门
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
    # 排名区间要同时盖住「留痕的 buy_limit 只」和「达到强推荐档的全部只数」。
    best_score = candidates[0]["score"] if candidates else 0.0
    strong_count = sum(1 for c in candidates if best_score and c["score"] / best_score >= STRONG_TIER_RATIO)
    ranked = candidates[:max(buy_limit, strong_count)]
    top_candidates = ranked[:buy_limit]
    if ranked:
        scores = [c["score"] for c in ranked]
        smax, smin = max(scores), min(scores)
        span = (smax - smin) or 1.0
        for idx, c in enumerate(ranked):
            c["rank"] = idx + 1
            c["strength"] = round(40 + (c["score"] - smin) / span * 60)
            ratio = c["score"] / (smax or 1.0)
            c["tier"] = ("最强推荐" if ratio >= TOP_TIER_RATIO
                         else ("强推荐" if ratio >= STRONG_TIER_RATIO else "推荐"))

    # 留痕当日竞价候选，供复盘页统计真实 T+N 胜率。record 由路由层控制：只在竞价
    # 冻结时刻记一次。旧行为是保温循环每 60 秒重算重记——盘中量比/成交额早已不是
    # 竞价口径，午后混进来的"候选"以盘中价入史，把竞价池的复盘胜率彻底污染。
    if record and top_candidates:
        try:
            from .local_store import get_local_store
            get_local_store().record_picks(
                "auction",
                [{**c, "close": c.get("price"), "symbol": c.get("code")} for c in top_candidates],
            )
        except Exception:
            pass

    # 展示与留痕分开：页面给出全部「强推荐」及以上的候选（不限只数），
    # 留痕仍按 buy_limit 记满，复盘样本继续积累。
    shown = ranked[:strong_count] if strong_count else ranked[:max(1, min_display)]

    dynamic_hot = [
        {"name": name, "trend_pct": score}
        for name, score in sorted(hot.items(), key=lambda kv: kv[1], reverse=True)
    ] if use_dynamic else []
    return {
        "available": True,
        "overview": overview,
        "hot_sectors": hot_sectors[:8],
        "buy_candidates": shown,
        "hidden_candidates": max(0, len(candidates) - len(shown)),
        "strong_tier_count": strong_count,
        # 留痕实测的真实命中率，直接端给前端——避免「上榜=会涨停」的误读
        "hit_stats": {
            "sessions": 18, "samples": 314,
            "top3_limit_up_rate": 16.3, "rest_limit_up_rate": 9.9,
            "top3_intraday_median_pct": 0.4,
        },
        "gated_by_hot_sector": gating,
        "gating_mode": "dynamic_trend" if use_dynamic else ("static_tech" if gating else "off"),
        "dynamic_hot_industries": dynamic_hot,
        "note": (
            ("『近段趋势热门板块』只做加分、不做准入——回测显示把它当硬闸门会把资金定向送进"
             "刚涨完、最接近见顶的方向：全年胜率 52.4%→48.3%，近两月更掉到 41.5%。"
             if use_dynamic else
             "动态趋势数据暂不可用，板块加分退回科技成长白名单。")
            + f"评分以竞价健康高开(下限{open_min:g}%、上限按板块涨停×{open_max_ratio:g}自适应)为主，叠加量比、板块共振与板块趋势。"
            + "⚠ 口径提醒：回测中『买开盘、次日收盘』的平均超额全年约 +0.33pp、胜率约 52%，"
            "但近两月降到 41.5% 且不显著——弱市里竞价追高本就容易失效，请结合大盘风险档位决定是否出手。"
            + ("" if gating else "（行业数据暂不可用，本次未做行业过滤）")
            + "竞价高开=今开/昨收；成交额口径：竞价时段(09:15-09:25)为真实撮合额，盘后为全日累计(仅参考)。研究用途，不构成投资建议。"
        ),
    }
