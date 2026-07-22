"""集合竞价"盘口轨迹"取数与四形态识别。

现有竞价功能只有 09:25 最终撮合价一个点——四形态（主力抢筹/诱多出货/洗盘低吸/多空分歧）
全长一个样。四形态的本质是 09:15-09:25 这 10 分钟里"临时撮合价怎么变"：
  · 主力抢筹：临时价一路走高 —— 资金持续挂买；
  · 诱多出货：高开后临时价被砸回 / 一路走低 —— 拉高引诱接盘再撤单；
  · 洗盘低吸：先探底再回升 —— 打掉浮筹低位吸筹；
  · 多空分歧：价格反复上下、无明确方向 —— 多空争夺无合力。

取数：东财盘前分时 push2his/trends2（iscr=1）直接给出 09:15-09:25 逐分钟虚拟撮合价，
**盘后仍可回溯当日轨迹**。因此不需要后端在竞价窗口在线逐秒采样——按需拉取即可，进程
重启、盘中才打开页面都不影响结果。竞价段成交量/额东财恒为 0，故形态只用价格轨迹判定。

注意：09:15 那一分钟（以及此后连续等于昨收的分钟）是"尚无有效申报"的占位，不是"平开"，
必须剔除后再判形态，否则宁德时代这种 09:21 才开始报价的票会被算成一路平开。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

import requests

_EM_TRENDS_URL = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
_EM_UT = "fa5fd1943c7b386f172d6893dbfba10b"
_HEADERS = {"User-Agent": "Mozilla/5.0"}
_AUCTION_MINUTES = {f"09:{m:02d}" for m in range(15, 26)}

# 当日轨迹一经 09:25 撮合就不再变化，进程内缓存到隔日：{"date": "2026-07-22", "items": {code: result}}
_CACHE: Dict[str, object] = {"date": None, "items": {}}


def _secid(code: str) -> str:
    """东财 secid 市场前缀：沪市(6/68/5/11 开头) 为 1，深市/北交所为 0。"""
    return f"1.{code}" if code.startswith(("60", "68", "51", "58", "11")) else f"0.{code}"


def fetch_auction_trend(code: str, timeout: float = 8.0) -> Tuple[float, List[Tuple[str, float]]]:
    """拉当日 09:15-09:25 竞价轨迹。返回 (昨收, [(hh:mm, 虚拟撮合价), …])，失败返回 (0.0, [])。

    已剔除开头"尚无有效申报"的占位分钟（价格恒等于昨收的前缀段）。
    """
    code = str(code).zfill(6)
    params = {
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "ut": _EM_UT, "ndays": 1, "iscr": 1, "iscca": 0, "secid": _secid(code),
    }
    session = requests.Session()
    session.trust_env = False  # 本机代理会挡掉东财，见 CLAUDE.md 取数约定
    try:
        resp = session.get(_EM_TRENDS_URL, params=params, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
        data = resp.json().get("data") or {}
    except (requests.RequestException, ValueError):
        return 0.0, []

    try:
        prev_close = float(data.get("preClose") or 0)
    except (TypeError, ValueError):
        return 0.0, []
    if prev_close <= 0:
        return 0.0, []

    points: List[Tuple[str, float]] = []
    for row in data.get("trends") or []:
        parts = str(row).split(",")
        if len(parts) < 3:
            continue
        hhmm = parts[0][-5:]
        if hhmm not in _AUCTION_MINUTES:
            continue
        try:
            points.append((hhmm, float(parts[2])))  # parts[2] = 该分钟末的虚拟撮合价
        except ValueError:
            continue

    # 剔除开头恒等于昨收的占位段（09:15 必是占位；有的票要到 09:21 才有有效申报）
    start = 0
    while start < len(points) and abs(points[start][1] - prev_close) < 1e-9:
        start += 1
    return prev_close, points[start:]


def classify_trajectory(points: Sequence[Tuple[str, float]], prev_close: float) -> dict:
    """从一只票的竞价价格轨迹判四形态。纯函数，可单测。

    points: [(hh:mm, 虚拟撮合价), …] 时间升序，已剔除占位段；prev_close: 昨收。
    返回 {pattern, label, confidence, gap_open, gap_last, drift, amplitude, reversals, note}
    """
    if prev_close <= 0 or len(points) < 3:
        return {"pattern": "insufficient", "label": "数据不足", "confidence": 0.0,
                "gap_open": None, "gap_last": None, "drift": None,
                "note": "竞价有效报价不足 3 分钟，无法判形态"}

    prices = [p for _, p in points]
    gap_open = (prices[0] / prev_close - 1.0) * 100.0        # 首个有效报价的高开幅度
    gap_last = (prices[-1] / prev_close - 1.0) * 100.0       # 09:25 撮合价 = 当日开盘价
    drift = gap_last - gap_open                              # 窗口内价格漂移(百分点)
    lo, hi = min(prices), max(prices)
    gap_low = (lo / prev_close - 1.0) * 100.0
    amplitude = (hi - lo) / prev_close * 100.0

    reversals = 0
    last_dir = 0
    for a, b in zip(prices, prices[1:]):
        d = (b > a) - (b < a)
        if d != 0 and last_dir != 0 and d != last_dir:
            reversals += 1
        if d != 0:
            last_dir = d

    def _pack(pattern, label, conf, note):
        return {"pattern": pattern, "label": label, "confidence": round(conf, 2),
                "gap_open": round(gap_open, 2), "gap_last": round(gap_last, 2),
                "drift": round(drift, 2), "amplitude": round(amplitude, 2),
                "reversals": reversals, "note": note}

    # 多空分歧优先判：反复拉锯且最终回到起点附近。漂移一旦够大（≥1.5pt）方向就是主线，
    # 中途震荡几次不改变性质——否则 "起+5.06% 收+2.60%" 这种明确被砸会误判成分歧。
    if (reversals >= 3 and abs(drift) < 1.5) or (abs(drift) < 0.4 and amplitude >= 1.5):
        return _pack("divergence", "多空分歧", min(0.9, 0.5 + reversals * 0.1),
                     "竞价价格反复拉锯、缺乏合力，宜等开盘量价确认再动手")

    # 洗盘低吸：先明显探底、末段大幅回升，且要收回起点附近（drift ≥ -0.3）。
    # 少了最后这条，"起+3.06% 砸到 -1.9% 反弹到 -0.38%" 这种崩塌中的反抽会被误标成看多。
    if gap_low <= gap_open - 0.3 and gap_last - gap_low >= 0.8 and drift >= -0.3:
        return _pack("shakeout", "洗盘低吸", min(0.85, 0.5 + (gap_last - gap_low) * 0.1),
                     "竞价探底后逐步回升，疑似打掉浮筹低位吸筹，可低吸关注")

    # 主力抢筹：一路走高且收在昨收之上（无明显探底，与洗盘区分）
    if drift >= 0.6 and gap_last > 0:
        return _pack("accumulation", "主力抢筹", min(0.95, 0.6 + drift * 0.1),
                     "竞价临时价逐步走高，资金持续挂买，强势方向")

    # 诱多出货：竞价一路被砸低（含"高开后砸回"和"直接跳水"）
    if drift <= -0.6:
        label = "诱多出货" if gap_open >= 2.0 else "竞价跳水"
        return _pack("distribution", label, min(0.9, 0.55 + abs(drift) * 0.1),
                     "竞价价格持续走低，抛压主导，警惕冲高回落")

    return _pack("neutral", "方向不明", 0.3,
                 "竞价无明显主力特征，按普通高/低开对待")


def classify_symbol(code: str) -> dict:
    """拉当日竞价轨迹并判形态（带当日缓存）。"""
    return classify_symbols([code]).get(str(code).zfill(6), classify_trajectory([], 0.0))


def classify_symbols(codes: Sequence[str], max_workers: int = 12) -> Dict[str, dict]:
    """批量判形态：并发拉盘前分时，同一交易日内进程级缓存（轨迹 09:25 后不再变化）。"""
    today = date.today().isoformat()
    if _CACHE.get("date") != today:
        _CACHE["date"] = today
        _CACHE["items"] = {}
    cache: Dict[str, dict] = _CACHE["items"]  # type: ignore

    wanted = [str(c).zfill(6) for c in codes if str(c).zfill(6).isdigit()]
    missing = [c for c in dict.fromkeys(wanted) if c not in cache]
    if missing:
        def _one(code: str) -> Tuple[str, dict]:
            prev_close, points = fetch_auction_trend(code)
            return code, classify_trajectory(points, prev_close)

        with ThreadPoolExecutor(max_workers=min(max_workers, len(missing))) as pool:
            for code, res in pool.map(_one, missing):
                cache[code] = res
    return {c: cache[c] for c in wanted if c in cache}


def tape_summary(results: Optional[Dict[str, dict]] = None) -> dict:
    """候选池形态概况（供竞价页展示）。results 缺省时用当日缓存里已判过的票。"""
    items: Dict[str, dict] = results if results is not None else dict(_CACHE.get("items") or {})  # type: ignore
    counts = {"accumulation": 0, "distribution": 0, "shakeout": 0, "divergence": 0,
              "neutral": 0, "insufficient": 0}
    for res in items.values():
        counts[res["pattern"]] = counts.get(res["pattern"], 0) + 1
    resolved = len(items) - counts["insufficient"]
    return {
        "date": _CACHE.get("date"),
        "tracked": len(items),
        "resolved": resolved,
        "pattern_counts": counts,
        "per_symbol": items,
        "available": resolved > 0,
    }
