"""盘中信号：用日线结构底座和实时快照生成状态化入场信号。

职责边界：
- 只做研究型推荐，不自动下单；
- 盘中仅使用当时可见的行情快照，信号价就是触发时价格；
- “主力行为”只作为量价代理证据，不声称识别真实账户；
- 未经样本外校准前只输出信号强度，不伪装成上涨概率。
"""
from __future__ import annotations

from datetime import datetime, timedelta
import math
import statistics
from typing import Any, Dict, Iterable, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo

from .industry import SECTOR_MIN_MEMBERS, SECTOR_MIN_RANKED, sector_rank
from .local_store import LocalQuantStore, get_local_store


_TZ = ZoneInfo("Asia/Shanghai")
# 板块共振在雷达总分里的权重。0.20 是历史值；调高 = 更跟板块轮动、更少被单票量价带偏。
# 走环境变量而不是改这里的默认值：这个数**没有回测依据**（雷达是盘中口径，而库里还没有
# 盘中历史，2026-08-05 才开始按日落 14:30 全市场快照）。攒够样本前它只能靠盘面观察定，
# 所以要能随时改、随时回退，而不是把一个拍脑袋的数字焊死在代码里。
_SECTOR_WEIGHT_DEFAULT = 0.20


def _sector_weight() -> float:
    import os
    return _clip(_f(os.getenv("LYNX_RADAR_SECTOR_WEIGHT"), _SECTOR_WEIGHT_DEFAULT), 0.0, 0.6)


def _env_num(name: str, default: float) -> float:
    """数值型环境变量；写错格式就按默认值，不让一个手滑的字符串把雷达停掉。"""
    import os
    return _f(os.getenv(name), default)


ACTIVE_STATUSES = {"watch", "entry", "unbuyable"}
STATUS_LABELS = {
    "watch": "提前预警",
    "entry": "入场触发",
    "unbuyable": "不可追入",
    "invalid": "信号失效",
}


def _f(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
        return default if not math.isfinite(number) else number
    except (TypeError, ValueError):
        return default


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def trading_phase(now: Optional[datetime] = None) -> str:
    """Return the A-share session phase for an Asia/Shanghai timestamp."""
    current = now or datetime.now(_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_TZ)
    else:
        current = current.astimezone(_TZ)
    if current.weekday() >= 5:
        return "closed"
    minute = current.hour * 60 + current.minute
    if 9 * 60 + 15 <= minute <= 9 * 60 + 25:
        return "auction"
    if 9 * 60 + 25 < minute < 9 * 60 + 30:
        return "opening_wait"
    if 9 * 60 + 30 <= minute <= 11 * 60 + 30:
        return "morning"
    if 13 * 60 <= minute < 14 * 60 + 57:
        return "afternoon"
    if 14 * 60 + 57 <= minute <= 15 * 60:
        return "closing_auction"
    return "closed"


def is_scan_window(now: Optional[datetime] = None) -> bool:
    return trading_phase(now) in {"auction", "opening_wait", "morning", "afternoon", "closing_auction"}


def session_progress(now: datetime) -> float:
    """Trading-minute progress, used only as a fallback intraday volume normalizer."""
    current = now if now.tzinfo else now.replace(tzinfo=_TZ)
    current = current.astimezone(_TZ)
    minute = current.hour * 60 + current.minute
    if minute < 9 * 60 + 30:
        return 0.04
    if minute <= 11 * 60 + 30:
        return max(0.04, min(0.5, (minute - (9 * 60 + 30)) / 240))
    if minute < 13 * 60:
        return 0.5
    return max(0.5, min(1.0, (120 + minute - 13 * 60) / 240))


def _limit_percent(symbol: str) -> float:
    if symbol.startswith(("300", "301", "688", "689")):
        return 20.0
    if symbol.startswith(("4", "8", "92")):
        return 30.0
    return 10.0


def _phase_label(phase: str) -> str:
    return {
        "auction": "集合竞价",
        "opening_wait": "等待开盘",
        "morning": "早盘连续竞价",
        "afternoon": "午后连续竞价",
        "closing_auction": "收盘集合竞价",
        "closed": "非交易时段",
    }.get(phase, phase)


def load_intraday_baselines(
    store: Optional[LocalQuantStore] = None,
    as_of: Optional[datetime] = None,
    industry_map: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Bulk-load yesterday-and-earlier structure metrics for the full market."""
    target = store or get_local_store()
    current = as_of or datetime.now(_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_TZ)
    trade_date = current.astimezone(_TZ).strftime("%Y-%m-%d")
    cutoff = (current.date() - timedelta(days=170)).strftime("%Y-%m-%d")
    rows = target._conn().execute(
        """
        SELECT k.symbol,k.date,k.open,k.high,k.low,k.close,k.volume,k.amount,
               COALESCE(m.name,''),COALESCE(m.industry,''),COALESCE(m.list_date,'')
        FROM daily_kline k
        LEFT JOIN stock_meta m ON m.symbol=k.symbol
        WHERE k.amount>0 AND k.date>=? AND k.date<?
        ORDER BY k.symbol,k.date DESC
        """,
        (cutoff, trade_date),
    ).fetchall()

    grouped: Dict[str, list[tuple]] = {}
    meta: Dict[str, tuple[str, str, str]] = {}
    for symbol, date, open_, high, low, close, volume, amount, name, industry, list_date in rows:
        code = str(symbol).zfill(6)
        bucket = grouped.setdefault(code, [])
        if len(bucket) < 65:
            bucket.append((str(date), _f(open_), _f(high), _f(low), _f(close), _f(volume), _f(amount)))
        meta[code] = (str(name or ""), str(industry or ""), str(list_date or ""))

    output: Dict[str, Dict[str, Any]] = {}
    for symbol, bars in grouped.items():
        if len(bars) < 20:
            continue
        closes = [row[4] for row in bars]
        highs = [row[2] for row in bars]
        amounts = [row[6] for row in bars if row[6] > 0]
        if closes[0] <= 0 or len(amounts) < 20:
            continue
        name, stored_industry, list_date = meta.get(symbol, ("", "", ""))
        industry = str((industry_map or {}).get(symbol) or stored_industry or "")
        ma5 = statistics.fmean(closes[:5])
        ma20 = statistics.fmean(closes[:20])
        ma60 = statistics.fmean(closes[:60]) if len(closes) >= 60 else ma20
        output[symbol] = {
            "symbol": symbol,
            "name": name or symbol,
            "industry": industry or "其他",
            "list_date": list_date,
            "last_bar_date": bars[0][0],
            "prev_close": closes[0],
            "ma5": ma5,
            "ma20": ma20,
            "ma60": ma60,
            "high20": max(highs[:20]),
            "high60": max(highs[:60]) if len(highs) >= 60 else max(highs),
            "amount_ma20": statistics.fmean(amounts[:20]),
            "return5": (closes[0] / closes[5] - 1) * 100 if len(closes) > 5 and closes[5] > 0 else 0.0,
            "return20": (closes[0] / closes[20] - 1) * 100 if len(closes) > 20 and closes[20] > 0 else 0.0,
        }
    return output


class IntradaySignalEngine:
    """Stateful full-market scanner. One instance should process snapshots serially."""

    def __init__(
        self,
        baselines: Optional[Dict[str, Dict[str, Any]]] = None,
        store: Optional[LocalQuantStore] = None,
        # 20 日均额下限，与一键智选的第一层筛选（screening.DEFAULT_MIN_AMOUNT）统一到
        # 5000 万。它与「当日成交额进前 15%」那道是两回事：当日分位拦不住「平时没人交易、
        # 某天突然放一次量」的票，而那种票正是买得进卖不出的。2026-08-06 实测从 3000 万
        # 提到 5000 万只少 2 个信号（21→19），入选票 20 日均额最低值从 0.45 亿升到 0.92 亿。
        min_daily_amount: float = 50_000_000,
        industry_map: Optional[Dict[str, str]] = None,
    ) -> None:
        self.store = store
        self.baselines = baselines or {}
        self.industry_map = industry_map or {}
        self.min_daily_amount = min_daily_amount
        self.baseline_date = ""
        self.trade_date = ""
        self.previous_quotes: Dict[str, Dict[str, float]] = {}
        self.states: Dict[str, Dict[str, Any]] = {}
        self.recent_events: list[Dict[str, Any]] = []

    def ensure_baselines(self, now: datetime, force: bool = False) -> None:
        if self.baselines and self.store is None and not force:
            return
        today = now.astimezone(_TZ).strftime("%Y-%m-%d")
        if force or not self.baselines or self.baseline_date != today:
            self.baselines = load_intraday_baselines(self.store, now, self.industry_map)
            self.baseline_date = today

    def restore(self, events: Iterable[Dict[str, Any]]) -> None:
        """Restore today's last known active states after a backend restart."""
        for event in events:
            item = event.get("item") or {}
            symbol = str(event.get("symbol") or item.get("symbol") or "").zfill(6)
            status = str(event.get("status") or item.get("status") or "")
            if not symbol or symbol in self.states:
                continue
            if status in ACTIVE_STATUSES:
                restored = dict(item)
                restored["status"] = status
                restored["candidate_streak"] = 2 if status == "entry" else 1
                self.states[symbol] = restored
        self.recent_events = list(events)[:80]

    def _new_event(self, item: Dict[str, Any], now: datetime) -> Dict[str, Any]:
        return {
            "event_id": uuid4().hex,
            "trade_date": now.astimezone(_TZ).strftime("%Y-%m-%d"),
            "symbol": item["symbol"],
            "name": item["name"],
            "status": item["status"],
            "triggered_at": item["triggered_at"],
            "signal_price": item["signal_price"],
            "score": item["score"],
            "item": dict(item),
        }

    def scan(
        self,
        snapshot: Dict[str, Dict[str, Any]],
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        current = now or datetime.now(_TZ)
        if current.tzinfo is None:
            current = current.replace(tzinfo=_TZ)
        current = current.astimezone(_TZ)
        today = current.strftime("%Y-%m-%d")
        if self.trade_date != today:
            self.trade_date = today
            self.previous_quotes.clear()
            self.states.clear()
            self.recent_events.clear()
        self.ensure_baselines(current)

        phase = trading_phase(current)
        continuous = phase in {"morning", "afternoon"}
        progress = session_progress(current)
        as_of = current.isoformat(timespec="seconds")

        valid: Dict[str, Dict[str, Any]] = {}
        market_returns: list[float] = []
        sector_returns: Dict[str, list[float]] = {}
        for raw_symbol, quote in snapshot.items():
            symbol = str(raw_symbol).zfill(6)
            base = self.baselines.get(symbol)
            if not base:
                continue
            name = str(quote.get("name") or base.get("name") or symbol)
            if "ST" in name.upper() or "退" in name:
                continue
            price = _f(quote.get("price") or quote.get("close"))
            prev_close = _f(quote.get("prev_close"), _f(base.get("prev_close")))
            if price <= 0 or prev_close <= 0:
                continue
            pct = _f(quote.get("change_percent") if quote.get("change_percent") is not None else quote.get("pct_chg"))
            if not pct:
                pct = (price / prev_close - 1) * 100
            enriched = dict(quote)
            enriched.update({"symbol": symbol, "name": name, "price": price, "prev_close": prev_close, "pct": pct})
            valid[symbol] = enriched
            market_returns.append(pct)
            sector_returns.setdefault(str(base.get("industry") or "其他"), []).append(pct)

        # 当日成交额的横截面分位（0..1）。用于「主力票」并列通道：那批票天天二十几亿，
        # 相对自己放不出量，但绝对成交额就是全市场最前面的一批。
        amt_pairs = sorted(
            (_f(q.get("amount")), s) for s, q in valid.items() if _f(q.get("amount")) > 0
        )
        amount_pctl = {
            sym: (i + 1) / len(amt_pairs) for i, (_a, sym) in enumerate(amt_pairs)
        } if amt_pairs else {}

        # 概念分位：与行业并列，取较强者用于「主力票」通道（见下）。概念映射拿不到就退回空，
        # 只用行业，不影响主流程。
        concept_ranks: Dict[str, float] = {}
        try:
            from .concept_lookup import code_concept_map
            from .industry import live_theme_ranks

            concept_ranks = {
                sym: rank for sym, (rank, _name) in
                live_theme_ranks(valid, [code_concept_map()]).items()
            }
        except Exception:  # noqa: BLE001 — 概念映射不可用时退回纯行业口径
            concept_ranks = {}

        market_median = statistics.median(market_returns) if market_returns else 0.0
        breadth_up = sum(1 for value in market_returns if value > 0) / len(market_returns) if market_returns else 0.0
        sector_stats = {
            industry: {
                "mean": statistics.fmean(values),
                "breadth": sum(1 for value in values if value > 0) / len(values),
                "count": len(values),
            }
            for industry, values in sector_returns.items()
        }
        # 板块强弱的**横截面分位**（0..1，只在成分 >=3 的板块之间排）。
        # 原先直接用绝对涨幅 clip(mean*6, -18, 18)：板块涨到 3% 就封顶，于是 +6% 的贵金属
        # 与任意 +3% 的板块同分，乘 0.20 权重后最终只差不到 1 分 —— 板块共振等于没进排序。
        # 分位是无量纲的：不管今天全市场是普涨还是普跌，"最强的那批板块"总能被排出来。
        # 板块强弱用横截面分位（与智选盘中重排共用 industry.sector_rank，同一套口径）。
        # 可排板块太少就没有横截面可言（只会出现在单票/小样本扫描里，生产每轮扫全市场、
        # 上百个板块）。这种退化场景退回绝对涨幅口径，而不是把所有板块压成中性。
        ranks = sector_rank(sector_stats)
        sector_rankable = sum(
            1 for s in sector_stats.values() if s["count"] >= SECTOR_MIN_MEMBERS
        ) >= SECTOR_MIN_RANKED
        for name, stat in sector_stats.items():
            stat["rank"] = ranks.get(name, 0.5)

        events: list[Dict[str, Any]] = []
        for symbol, quote in valid.items():
            base = self.baselines[symbol]
            name = quote["name"]
            price = quote["price"]
            prev_close = quote["prev_close"]
            pct = quote["pct"]
            open_price = _f(quote.get("open"), prev_close)
            high = _f(quote.get("high"), price)
            low = _f(quote.get("low"), min(open_price, price))
            amount = _f(quote.get("amount"))
            amount_ma20 = _f(base.get("amount_ma20"))
            if price < 2 or amount_ma20 < self.min_daily_amount:
                continue

            feed_volume_ratio = _f(quote.get("volume_ratio"))
            projected_ratio = amount / max(progress, 0.04) / amount_ma20 if amount_ma20 > 0 and amount > 0 else 0.0
            activity_ratio = (
                feed_volume_ratio * 0.7 + projected_ratio * 0.3
                if feed_volume_ratio > 0 and projected_ratio > 0
                else max(feed_volume_ratio, projected_ratio)
            )
            activity_ratio = min(8.0, max(0.0, activity_ratio))

            day_range = max(0.0, high - low)
            range_position = (price - low) / day_range if day_range > 0 else 0.5
            high20 = _f(base.get("high20"), prev_close)
            high60 = _f(base.get("high60"), high20)
            breakout20 = price >= high20 * 1.002
            breakout60 = price >= high60 * 1.002

            previous = self.previous_quotes.get(symbol)
            speed_1m = 0.0
            if previous and previous.get("price", 0) > 0:
                seconds = max(1.0, current.timestamp() - previous.get("timestamp", current.timestamp()))
                if seconds <= 180:
                    speed_1m = (price / previous["price"] - 1) * 100 * 60 / seconds

            trend_score = 48.0
            if prev_close >= _f(base.get("ma20")):
                trend_score += 12
            if _f(base.get("ma5")) >= _f(base.get("ma20")):
                trend_score += 10
            if _f(base.get("ma20")) >= _f(base.get("ma60")):
                trend_score += 8
            if _f(base.get("return20")) > 35:
                trend_score -= 10
            trend_score = _clip(trend_score)

            if activity_ratio >= 3:
                volume_score = 92.0
            elif activity_ratio >= 2:
                volume_score = 82.0
            elif activity_ratio >= 1.5:
                volume_score = 72.0
            elif activity_ratio >= 1.15:
                volume_score = 60.0
            else:
                volume_score = 38.0 + activity_ratio * 10
            # 绝对成交额已经排在全市场最前面的票，量能分不能只按「相对自己有没有放大」打。
            # 中巨芯 20 日均额 24.4 亿，涨停当天量能倍数 1.03 —— 按相对口径只有 48 分，
            # 把总分压到 80 以下，于是并列通道开了门它也进不来。这里给一个下限，
            # 让「本来就是全市场成交最大的一批」这件事本身算作量能证据。
            amount_rank_for_volume = amount_pctl.get(symbol, 0.0)
            if amount_rank_for_volume >= 0.95:
                volume_score = max(volume_score, 55.0 + (amount_rank_for_volume - 0.95) / 0.05 * 25.0)

            price_score = 48 + min(max(pct, -4), 8) * 4
            price_score += max(0.0, range_position - 0.5) * 28
            if price >= open_price:
                price_score += 5
            if breakout20:
                price_score += 10
            if breakout60:
                price_score += 5
            if speed_1m >= 0.25:
                price_score += min(8, speed_1m * 6)
            price_score = _clip(price_score)

            industry = str(base.get("industry") or "其他")
            sector = sector_stats.get(industry, {"mean": 0.0, "breadth": 0.5, "count": 0, "rank": 0.5})
            if sector_rankable:
                context_score = 48 + (_f(sector.get("rank"), 0.5) - 0.5) * 44
            else:
                context_score = 48 + _clip(_f(sector["mean"]) * 6, -18, 18)
            context_score += (_f(sector["breadth"], 0.5) - 0.5) * 20
            context_score += (breadth_up - 0.5) * 16
            context_score = _clip(context_score)

            w_ctx = _sector_weight()
            # 板块权重可调，其余三项按原比例（0.32:0.27:0.21）分摊剩下的份额，
            # 这样调板块权重不会连带改变量价与趋势之间的相对关系。
            rest = (1.0 - w_ctx) / 0.80
            score = (
                price_score * 0.32 * rest
                + volume_score * 0.27 * rest
                + trend_score * 0.21 * rest
                + context_score * w_ctx
                + (4 if breakout20 else 0)
            )
            score = round(_clip(score), 1)

            limit_pct = _limit_percent(symbol)
            distance_to_limit = limit_pct - pct
            at_limit = distance_to_limit <= 0.4 or (
                high > 0 and abs(price - high) / price < 0.0005 and distance_to_limit <= 0.8
            )
            near_limit = distance_to_limit <= 1.5
            projected_liquid = amount_ma20 >= self.min_daily_amount
            basic_setup = (
                pct >= 1.0
                and range_position >= 0.68
                and activity_ratio >= 1.50
                and projected_liquid
                and price >= open_price * 0.995
            )
            # —— 「主力票」并列通道（2026-08-05）——
            # 现有通道要求量能相对自己放大 ≥1.5 倍，抓的是「突然被资金注意到」的票。
            # 但当日实测：中巨芯 20 日均额 24.4 亿、今日涨 20%，量能倍数只有 1.03；
            # 有研硅 26.2 亿 / 1.15 倍；神工股份 16.3 亿 / 1.11 倍 —— 它们本来就是全市场
            # 成交最大的一批，天天这个量，涨停也放不出「相对大量」，于是全部未触发。
            # 这条并列通道改用**绝对**证据：成交额进全市场前 X% + 涨幅居前 + 主题分位高。
            # 注意是 or 不是放松：原通道的阈值一个没动，只是多开一扇门。
            amount_rank = amount_pctl.get(symbol, 0.0)
            # 主题取行业与概念的较强者，与智选同口径：正帆科技的行业是「专用设备」(0.72)，
            # 但它今天的热点身份在概念「存储芯片」(0.98)。只看行业会漏掉这一类。
            theme_rank = max(_f(sector.get("rank"), 0.5), _f(concept_ranks.get(symbol), 0.0))
            heavyweight_setup = (
                pct >= _env_num("LYNX_RADAR_HEAVY_MIN_PCT", 6.0)
                and amount_rank >= _env_num("LYNX_RADAR_HEAVY_AMOUNT_PCTL", 0.97)
                and theme_rank >= _env_num("LYNX_RADAR_HEAVY_THEME", 0.90)
                and range_position >= 0.68        # 仍要求收在日内区间上部，冲高回落的不算
                and price >= open_price * 0.995   # 仍要求没跌破开盘，走坏的不算
                and projected_liquid
            )
            # 绝对流动性底线：不管走哪条通道，当日成交额必须进全市场前 X%。
            # 「量能倍数」是今日量 ÷ 自身 20 日均量，分母小的票天生容易出高倍数 ——
            # 2026-08-06 实测雷达推的 10 只里，大丰实业日均额 0.36 亿、今日 0.87 亿就算
            # 「放量 6.2 倍」拿满分量能分，而通富微电日均 142 亿、今日 61 亿只有 1.12 倍。
            # 于是雷达系统性地推没人交易的小票，用户根本不敢买。原来的门槛是日均 3000 万，
            # 形同虚设。这里改用当日成交额的**横截面分位**，随市场活跃度自适应。
            liquid_enough = amount_rank >= _env_num("LYNX_RADAR_MIN_AMOUNT_PCTL", 0.85)
            setup = (basic_setup or heavyweight_setup) and liquid_enough
            momentum_trigger = breakout20 or speed_1m >= 0.25 or (
                pct >= 2.0 and _f(sector["mean"]) >= 0.3
            ) or heavyweight_setup
            # 主力票走自己的分数线：它的证据种类不同（绝对成交额+主题地位，而不是相对放量），
            # 而总分里的趋势分算的是昨天收盘的均线关系 —— 今天刚启动的票那一项天然低。
            # 中巨芯当日实测总分 76.2，其中趋势分只有 48。这是并列通道自带的标准，
            # 不是把原通道的 80 分放松了：原通道一个字没改。
            score_gate = _env_num("LYNX_RADAR_HEAVY_MIN_SCORE", 74.0) if heavyweight_setup else 80.0
            prealert = setup and score >= score_gate and momentum_trigger
            # 主力票同样走不到 activity_ratio 1.75，正式触发对它们改看这条通道自身成立。
            formal = continuous and prealert and score >= 84 and (
                activity_ratio >= 1.75 or heavyweight_setup
            )

            prior_state = self.states.get(symbol, {})
            prior_status = str(prior_state.get("status") or "")
            streak = int(prior_state.get("candidate_streak") or 0) + 1 if formal else 0
            status = ""
            if prealert and (at_limit or near_limit):
                status = "unbuyable"
            elif formal and ((breakout20 and score >= 88) or streak >= 2):
                status = "entry"
            elif prealert:
                status = "watch"

            prior_invalidation = _f(prior_state.get("invalidation_price"))
            if prior_status in ACTIVE_STATUSES and not status:
                expired = False
                try:
                    valid_until_dt = datetime.fromisoformat(str(prior_state.get("valid_until") or ""))
                    if valid_until_dt.tzinfo is None:
                        valid_until_dt = valid_until_dt.replace(tzinfo=_TZ)
                    expired = current > valid_until_dt
                except ValueError:
                    pass
                if expired or price <= prior_invalidation or score < 55:
                    status = "invalid"
                else:
                    status = prior_status

            if not status:
                self.previous_quotes[symbol] = {
                    "price": price,
                    "amount": amount,
                    "timestamp": current.timestamp(),
                }
                continue

            trigger_level = high20 if breakout20 else max(open_price, prev_close)
            invalidation = prior_invalidation or max(trigger_level * 0.992, price * 0.975)
            previous_signal_price = _f(prior_state.get("signal_price"))
            same_signal = prior_status == status and previous_signal_price > 0
            signal_price = previous_signal_price if same_signal else price
            # 首次提醒那一刻的涨跌幅：和 signal_price 同进同退，卡片要能回答「提醒时它涨了
            # 多少、现在涨了多少」。老状态（加这个字段之前触发的）没有就留 None，
            # 前端隐藏 —— 宁可不显示，也不拿现在的涨幅冒充当时的。
            prior_signal_pct = prior_state.get("signal_pct_chg")
            signal_pct_chg = (
                round(_f(prior_signal_pct), 2)
                if same_signal and prior_signal_pct is not None
                else (None if same_signal else round(pct, 2))
            )
            first_seen = str(prior_state.get("first_seen") or as_of)
            triggered_at = str(prior_state.get("triggered_at") or as_of) if prior_status == status else as_of
            valid_minutes = 20 if status == "entry" else 10
            valid_until = (
                str(prior_state.get("valid_until"))
                if prior_status == status and prior_state.get("valid_until")
                else (current + timedelta(minutes=valid_minutes)).isoformat(timespec="seconds")
            )
            actionable = False
            if status == "entry" and continuous:
                try:
                    action_deadline = datetime.fromisoformat(valid_until)
                    if action_deadline.tzinfo is None:
                        action_deadline = action_deadline.replace(tzinfo=_TZ)
                    actionable = current <= action_deadline
                except ValueError:
                    actionable = False

            reasons = []
            if breakout60:
                reasons.append("放量突破60日压力位")
            elif breakout20:
                reasons.append("价格突破20日压力位")
            if activity_ratio >= 2:
                reasons.append(f"同时间量能约 {activity_ratio:.1f} 倍")
            elif activity_ratio >= 1.45:
                reasons.append(f"盘中量能放大至 {activity_ratio:.1f} 倍")
            if speed_1m >= 0.25:
                reasons.append(f"短时涨速 {speed_1m:.2f}%/分钟")
            if _f(sector["mean"]) >= 0.5:
                reasons.append(f"{industry}板块共振 {_f(sector['mean']):+.2f}%")
            if range_position >= 0.8:
                reasons.append("价格位于日内区间上部")
            if heavyweight_setup and activity_ratio < 1.5:
                # 说清楚它凭什么进来：不是放量进来的，是靠绝对成交额和主题地位。
                reasons.insert(0, f"主力票通道：成交额全市场前 {(1 - amount_rank) * 100:.0f}%、"
                                  f"{industry}主题分位 {_f(sector.get('rank'), 0.5) * 100:.0f}")
            if status == "unbuyable":
                reasons.insert(0, "距离涨停过近或已经封板，不再建议追入")
            if status == "invalid":
                reasons.insert(0, "价格或信号强度已跌破有效条件")

            item = {
                "symbol": symbol,
                "name": name,
                "industry": industry,
                "status": status,
                "status_label": STATUS_LABELS[status],
                "score": score,
                "current_price": round(price, 3),
                "signal_price": round(signal_price, 3),
                "signal_pct_chg": signal_pct_chg,
                "pct_chg": round(pct, 2),
                "entry_low": round(signal_price * 0.997, 3),
                "entry_high": round(signal_price * 1.006, 3),
                "chase_limit": round(min(signal_price * 1.015, prev_close * (1 + limit_pct / 100) - 0.01), 3),
                "invalidation_price": round(invalidation, 3),
                "distance_to_limit": round(max(0.0, distance_to_limit), 2),
                "activity_ratio": round(activity_ratio, 2),
                "heavyweight": bool(heavyweight_setup),
                "amount_percentile": round(amount_rank * 100, 1),
                "feed_volume_ratio": round(feed_volume_ratio, 2),
                "projected_amount_ratio": round(projected_ratio, 2),
                "range_position": round(range_position, 3),
                "speed_1m": round(speed_1m, 3),
                "breakout20": breakout20,
                "breakout60": breakout60,
                "sector_change": round(_f(sector["mean"]), 2),
                "market_median": round(market_median, 2),
                "reasons": reasons[:5],
                "phase": phase,
                "phase_label": _phase_label(phase),
                "first_seen": first_seen,
                "triggered_at": triggered_at,
                "last_seen": as_of,
                "valid_until": valid_until,
                "candidate_streak": streak,
                "quote_source": quote.get("quote_source") or "",
                "calibrated_probability": None,
                "signal_mode": "live",
                "actionable": actionable,
            }
            self.states[symbol] = item

            if prior_status != status:
                event = self._new_event(item, current)
                events.append(event)
                self.recent_events.insert(0, event)

            self.previous_quotes[symbol] = {
                "price": price,
                "amount": amount,
                "timestamp": current.timestamp(),
            }

        # Keep price history for non-candidates so acceleration can be measured next cycle.
        for symbol, quote in valid.items():
            if symbol not in self.previous_quotes:
                self.previous_quotes[symbol] = {
                    "price": quote["price"],
                    "amount": _f(quote.get("amount")),
                    "timestamp": current.timestamp(),
                }

        self.recent_events = self.recent_events[:80]
        active_items = []
        for item in self.states.values():
            if item.get("status") not in ACTIVE_STATUSES:
                continue
            try:
                last_seen = datetime.fromisoformat(str(item.get("last_seen") or ""))
                if last_seen.tzinfo is None:
                    last_seen = last_seen.replace(tzinfo=_TZ)
                if phase != "closed" and (current - last_seen).total_seconds() > 180:
                    continue
            except ValueError:
                continue
            active_items.append(dict(item))
        priority = {"entry": 0, "watch": 1, "unbuyable": 2}
        active_items.sort(key=lambda item: (priority.get(str(item.get("status")), 9), -_f(item.get("score"))))

        market_tone = "偏暖" if breadth_up >= 0.6 and market_median > 0 else (
            "偏冷" if breadth_up <= 0.4 and market_median < 0 else "中性"
        )
        return {
            "status": "live" if phase != "closed" else "closed",
            "as_of": as_of,
            "trade_date": today,
            "phase": phase,
            "phase_label": _phase_label(phase),
            "universe": len(valid),
            "candidate_count": len(active_items),
            "entry_count": sum(1 for item in active_items if item["status"] == "entry"),
            "watch_count": sum(1 for item in active_items if item["status"] == "watch"),
            "unbuyable_count": sum(1 for item in active_items if item["status"] == "unbuyable"),
            "market": {
                "tone": market_tone,
                "median_pct": round(market_median, 2),
                "breadth_up": round(breadth_up, 4),
            },
            "items": active_items,
            "events": events,
            "recent_events": list(self.recent_events),
            "method_note": "日线结构预计算 + 盘中量价快扫 + 板块/市场共振；已涨停或距离涨停过近不推荐追入。",
            "probability_note": "上涨概率尚未完成盘中样本外校准，当前仅展示可回放验证的信号强度。",
        }


def build_close_review(
    snapshot: Dict[str, Dict[str, Any]],
    baselines: Dict[str, Dict[str, Any]],
    now: Optional[datetime] = None,
    limit: int = 60,
) -> Dict[str, Any]:
    """Replay the closing snapshot without pretending it was a live intraday trigger."""
    current = now or datetime.now(_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_TZ)
    current = current.astimezone(_TZ)
    replay_at = current.replace(hour=14, minute=56, second=0, microsecond=0)
    engine = IntradaySignalEngine(baselines=baselines)

    # Two identical passes reproduce the normal confirmation state machine while
    # deliberately leaving short-term acceleration at zero.
    engine.scan(snapshot, replay_at)
    result = engine.scan(snapshot, replay_at + timedelta(seconds=15))
    safe_limit = max(1, min(int(limit), 200))
    reviewed_at = current.isoformat(timespec="seconds")
    labels = {
        "entry": "收盘复盘候选",
        "watch": "收盘复盘观察",
        "unbuyable": "收盘不可追",
    }
    items: list[Dict[str, Any]] = []
    for raw_item in result.get("items") or []:
        item = dict(raw_item)
        item["status_label"] = labels.get(str(item.get("status")), str(item.get("status_label") or "收盘复盘"))
        item["signal_mode"] = "close_review"
        item["actionable"] = False
        item["reviewed_at"] = reviewed_at
        item["triggered_at"] = ""
        item["first_seen"] = ""
        item["last_seen"] = reviewed_at
        item["valid_until"] = ""
        item["phase"] = "closed_review"
        item["phase_label"] = "收盘复盘"
        item["reasons"] = ["依据今日收盘快照回放，非盘中实时触发"] + list(item.get("reasons") or [])[:4]
        items.append(item)
        if len(items) >= safe_limit:
            break

    result.update({
        "status": "closed",
        "as_of": reviewed_at,
        "trade_date": current.strftime("%Y-%m-%d"),
        "phase": "closed",
        "phase_label": "已收盘 · 显示今日复盘",
        "review_mode": "close_review",
        "items": items,
        "events": [],
        "recent_events": [],
        "candidate_count": len(items),
        "entry_count": sum(1 for item in items if item.get("status") == "entry"),
        "watch_count": sum(1 for item in items if item.get("status") == "watch"),
        "unbuyable_count": sum(1 for item in items if item.get("status") == "unbuyable"),
        "method_note": "依据今日收盘快照回放量价与结构条件；无法还原盘中逐分钟触发时点，因此与实时入场信号分开标注。",
        "probability_note": "收盘复盘候选用于次日观察，不代表明日开盘可直接买入；仍需等待下一交易日实时量价确认。",
    })
    return result
