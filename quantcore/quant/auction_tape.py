"""集合竞价"盘口轨迹"采样与四形态识别。

现有竞价功能只有 09:25 最终撮合价一个点——四形态（主力抢筹/诱多出货/洗盘低吸/多空分歧）
全长一个样。四形态的本质是 09:15-09:25 这 10 分钟里"临时撮合价 + 竞价量能怎么变"：
  · 主力抢筹：临时价逐步走高 + 量能放大 —— 资金持续挂买；
  · 诱多出货：高开后临时价逐步走低 / 量能萎缩 —— 拉高引诱接盘再撤；
  · 洗盘低吸：低开或探底后逐步回升 —— 打掉浮筹低位吸筹；
  · 多空分歧：价格反复上下、量能不稳 —— 多空争夺无合力。

实现：后端在 09:15-09:25 每 ~20 秒采一次全市场快照，攒出每只票的迷你时间序列，收盘或
盘中据此分类。**只能盘前实时用，无法历史回测**（不存历史竞价 tick）——这点必须在页面标清。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

# 内存态盘口：{"date": "2026-07-21", "prev_close": {sym: pc}, "samples": {sym: [(hhmmss, price, amount)]}}
_TAPE: Dict[str, object] = {"date": None, "prev_close": {}, "samples": {}}

# 只跟踪有意义的异动票（|高开| ≥ 此阈值），避免全市场 5000 只 × N 次采样撑爆内存
_TRACK_GAP_MIN = 1.0
_MAX_TRACK = 1200


def reset_if_new_day(today: Optional[str] = None) -> None:
    today = today or date.today().isoformat()
    if _TAPE.get("date") != today:
        _TAPE["date"] = today
        _TAPE["prev_close"] = {}
        _TAPE["samples"] = {}


def record_sample(snapshot: Dict[str, dict], now: Optional[datetime] = None) -> int:
    """采一次竞价快照：给异动票追加 (时刻, 临时撮合价, 竞价累计额)。返回本次跟踪的票数。

    临时撮合价用快照 price（竞价时段=最新撮合价）；量能用 amount（竞价时段=竞价累计成交额）。
    """
    now = now or datetime.now()
    reset_if_new_day(now.date().isoformat())
    hhmmss = now.strftime("%H%M%S")
    prev_close: Dict[str, float] = _TAPE["prev_close"]  # type: ignore
    samples: Dict[str, list] = _TAPE["samples"]  # type: ignore

    for v in snapshot.values():
        code = str(v.get("code") or v.get("symbol") or "").zfill(6)
        if len(code) != 6 or not code.isdigit():
            continue
        try:
            price = float(v.get("price") or 0)
            pc = float(v.get("prev_close") or 0)
            amount = float(v.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0 or pc <= 0:
            continue
        gap = (price / pc - 1.0) * 100.0
        already = code in samples
        # 新票只在异动时纳入跟踪（且不超上限）；已跟踪的票继续采集，保证轨迹连续
        if not already:
            if abs(gap) < _TRACK_GAP_MIN or len(samples) >= _MAX_TRACK:
                continue
            prev_close[code] = pc
            samples[code] = []
        samples[code].append((hhmmss, round(price, 3), round(amount, 1)))
    return len(samples)


def classify_trajectory(samples: List[Tuple[str, float, float]], prev_close: float) -> dict:
    """从一只票的竞价轨迹判四形态。纯函数，可单测。

    samples: [(hhmmss, price, cumulative_amount), …] 时间升序；prev_close: 昨收。
    返回 {pattern, label, confidence, gap_open, gap_last, drift, note}
    """
    if prev_close <= 0 or len(samples) < 3:
        return {"pattern": "insufficient", "label": "数据不足", "confidence": 0.0,
                "gap_open": None, "gap_last": None, "drift": None,
                "note": "竞价采样点不足，无法判形态"}

    prices = [s[1] for s in samples]
    amounts = [s[2] for s in samples]
    gap_open = (prices[0] / prev_close - 1.0) * 100.0
    gap_last = (prices[-1] / prev_close - 1.0) * 100.0
    drift = gap_last - gap_open                                # 窗口内临时价漂移(百分点)
    lo, hi = min(prices), max(prices)
    amplitude = (hi - lo) / prev_close * 100.0                 # 价格振幅(百分点)

    # 方向反转次数（choppiness）
    reversals = 0
    last_dir = 0
    for a, b in zip(prices, prices[1:]):
        d = (b > a) - (b < a)
        if d != 0 and last_dir != 0 and d != last_dir:
            reversals += 1
        if d != 0:
            last_dir = d

    # 量能趋势：累计额的后半段增量 vs 前半段增量（放大/萎缩）
    mid = len(amounts) // 2
    early_inc = amounts[mid] - amounts[0]
    late_inc = amounts[-1] - amounts[mid]
    vol_growing = late_inc > early_inc * 1.1
    vol_shrinking = late_inc < early_inc * 0.7

    def _pack(pattern, label, conf, note):
        return {"pattern": pattern, "label": label, "confidence": round(conf, 2),
                "gap_open": round(gap_open, 2), "gap_last": round(gap_last, 2),
                "drift": round(drift, 2), "amplitude": round(amplitude, 2),
                "reversals": reversals, "vol_growing": vol_growing, "note": note}

    # 多空分歧优先判：反复拉锯、无明确方向（大振幅 + 多次反转，或漂移极小但振幅大）
    if reversals >= 3 or (abs(drift) < 0.4 and amplitude >= 1.5):
        return _pack("divergence", "多空分歧", min(0.9, 0.5 + reversals * 0.1),
                     "竞价价格反复拉锯、缺乏合力，宜等开盘量价确认再动手")

    # 主力抢筹：临时价持续走高 + 量能放大（末价为正）
    if drift >= 0.6 and gap_last > 0 and vol_growing:
        return _pack("accumulation", "主力抢筹", min(0.95, 0.6 + drift * 0.1),
                     "竞价临时价逐步走高且量能放大，资金持续挂买，强势方向")

    # 诱多出货：明显高开后临时价走低 / 量能萎缩
    if gap_open >= 2.0 and (drift <= -0.6 or vol_shrinking):
        return _pack("distribution", "诱多出货", min(0.9, 0.55 + abs(drift) * 0.1),
                     "高开后竞价价格回落、量能萎缩，警惕拉高引诱接盘，冲高回落风险")

    # 洗盘低吸：低开/探底后回升
    if gap_open <= 0.5 and drift >= 0.8:
        return _pack("shakeout", "洗盘低吸", min(0.85, 0.5 + drift * 0.1),
                     "低开探底后竞价逐步回升，疑似打掉浮筹低位吸筹，可低吸关注")

    # 兜底：方向不明（温和高开/低开但无上述特征）
    return _pack("neutral", "方向不明", 0.3,
                 "竞价无明显主力特征，按普通高/低开对待")


def classify_symbol(code: str) -> dict:
    """从当日盘口轨迹判某票四形态（无轨迹返回数据不足）。"""
    code = str(code).zfill(6)
    samples: Dict[str, list] = _TAPE.get("samples") or {}  # type: ignore
    prev_close: Dict[str, float] = _TAPE.get("prev_close") or {}  # type: ignore
    return classify_trajectory(samples.get(code, []), prev_close.get(code, 0.0))


def tape_summary() -> dict:
    """当日采样概况 + 四形态计数（供竞价页展示）。"""
    samples: Dict[str, list] = _TAPE.get("samples") or {}  # type: ignore
    prev_close: Dict[str, float] = _TAPE.get("prev_close") or {}  # type: ignore
    counts = {"accumulation": 0, "distribution": 0, "shakeout": 0, "divergence": 0,
              "neutral": 0, "insufficient": 0}
    per_symbol: Dict[str, dict] = {}
    for code, seq in samples.items():
        res = classify_trajectory(seq, prev_close.get(code, 0.0))
        counts[res["pattern"]] = counts.get(res["pattern"], 0) + 1
        per_symbol[code] = res
    sample_points = max((len(seq) for seq in samples.values()), default=0)
    return {
        "date": _TAPE.get("date"),
        "tracked": len(samples),
        "sample_points": sample_points,
        "pattern_counts": counts,
        "per_symbol": per_symbol,
        "available": bool(samples),
    }
