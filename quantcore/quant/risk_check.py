"""七不买避雷：规则化风险体检（纯函数，仅日线 + 可选实时行情）。

来源是经典口诀「三不买七不卖」的买入侧，落成可回测的硬规则。设计约束：
- 全部板块感知（主板 10cm / 创科 20cm / 北交 30cm 的「急涨」阈值不同）；
- 「利好公开不买」需要可靠的个股新闻数据，当前不做（见 2026-07-17 spec 数据边界）；
- 「长期横盘」按提示而非风险——大基底盘整在突破前反而是机会（Stage 1 base）；
- 输出一律附免责，advice 是规则化提示，不构成投资建议。
"""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

RISK = "risk"
INFO = "info"

# 各规则阈值（集中放置便于回测校准；见 docs/superpowers/specs/2026-07-17-risk-check-design.md）
SURGE_WINDOW = 10          # 急涨观察窗（交易日）
SURGE_LIMIT_MULT = 5.0     # 累计涨幅 ≥ 涨停幅 × 5 判急涨（主板50%、创科100%）
SURGE_MAX_PULLBACK = 8.0   # 期间最大回撤 <8% 才算「无调整的急涨」
FLAT_WINDOW = 60
FLAT_RANGE_PCT = 12.0      # 60日振幅 <12% 判横盘
VOL_SPIKE_MULT = 4.0       # 当日量 ≥ 前5日均量×4 判天量
HIGH_PCTL = 0.8            # 「高位」= 收盘处于近60日 80 分位以上
STALL_VOL_MULT = 1.5       # 近5日均量 ≥ 再前5日 ×1.5
STALL_GAIN_PCT = 2.0       # 近5日涨幅 <2% 判滞涨
TURNOVER_SPIKE = 8.0       # 实时换手率 ≥8% 强化天量判定


def board_limit_pct(symbol: str) -> float:
    """日涨跌幅上限：科创688/689、创业300/301 = 20%；北交 8/4/920 = 30%；主板 = 10%。

    定义放在本轻量模块（仅依赖 pandas）：扫描 worker 子进程要 import 本模块，
    若经由重型 engine 引入会显著拖慢每个进程的冷启动。engine 从这里再导出。
    """
    sym = str(symbol or "")
    if sym.startswith(("688", "689", "300", "301")):
        return 20.0
    if sym.startswith(("8", "4", "920")):
        return 30.0
    return 10.0


def _pct_rank(series: pd.Series, value: float) -> float:
    s = series.dropna()
    if len(s) == 0:
        return 0.5
    return float((s < value).mean())


def check_risks(symbol: str, name: str, df: pd.DataFrame,
                quote: Optional[Dict] = None,
                bad_forecast: bool = False) -> Dict[str, object]:
    """对单只股票跑七不买规则。df 需含 close/volume 列（日线，升序）。

    返回 {flags, risk_count, advice}；数据不足（<60 根）只跑可跑的规则。
    """
    flags: List[Dict[str, object]] = []
    quote = quote or {}
    closes = df["close"].astype(float)
    vols = df["volume"].astype(float)
    n = len(df)
    close = float(closes.iloc[-1]) if n else 0.0
    limit_pct = board_limit_pct(symbol)

    # 1) 急涨后高位：近10日累计涨幅 ≥ 涨停幅×5 且期间最大回撤 <8%
    if n >= SURGE_WINDOW + 1 and close > 0:
        window = closes.tail(SURGE_WINDOW + 1)
        base = float(window.iloc[0])
        if base > 0:
            gain = (close / base - 1) * 100
            running_max = window.cummax()
            pullback = float(((running_max - window) / running_max).max()) * 100
            if gain >= limit_pct * SURGE_LIMIT_MULT and pullback < SURGE_MAX_PULLBACK:
                flags.append({
                    "key": "surge", "name": "急涨后高位", "level": RISK,
                    "reason": f"近{SURGE_WINDOW}日已涨 {gain:.0f}% 且几乎无调整（回撤 {pullback:.1f}%），追高易接出货",
                })

    # 2) 长期横盘（提示级：大基底突破前反而是机会，不判雷）
    if n >= FLAT_WINDOW:
        w = closes.tail(FLAT_WINDOW)
        lo = float(w.min())
        if lo > 0:
            rng = (float(w.max()) / lo - 1) * 100
            if rng < FLAT_RANGE_PCT:
                flags.append({
                    "key": "flat", "name": "长期横盘", "level": INFO,
                    "reason": f"近{FLAT_WINDOW}日振幅仅 {rng:.1f}%，缺乏资金关注；若为大基底盘整需等放量突破再介入",
                })

    # 高位判定（供天量/滞涨共用）
    at_high = n >= FLAT_WINDOW and _pct_rank(closes.tail(FLAT_WINDOW), close) >= HIGH_PCTL

    # 3) 天量：当日量 ≥ 前5日均量×4 且处于高位（实时换手率 ≥8% 时补充佐证）
    if n >= 6:
        prev5 = float(vols.iloc[-6:-1].mean() or 0)
        today_vol = float(vols.iloc[-1] or 0)
        turnover = float(quote.get("turnover_rate") or 0)
        if prev5 > 0 and today_vol >= prev5 * VOL_SPIKE_MULT and at_high:
            extra = f"，换手率 {turnover:.1f}%" if turnover >= TURNOVER_SPIKE else ""
            flags.append({
                "key": "volume_spike", "name": "高位放天量", "level": RISK,
                "reason": f"当日量为前5日均量 {today_vol / prev5:.1f} 倍且处于高位{extra}，警惕主力出逃",
            })

    # 4) 高位放量滞涨：近5日量增 ≥1.5× 但涨幅 <2%，且处于高位
    if n >= 11 and close > 0:
        vol_recent = float(vols.iloc[-5:].mean() or 0)
        vol_before = float(vols.iloc[-10:-5].mean() or 0)
        base5 = float(closes.iloc[-6])
        gain5 = (close / base5 - 1) * 100 if base5 > 0 else 0.0
        if vol_before > 0 and vol_recent >= vol_before * STALL_VOL_MULT \
                and gain5 < STALL_GAIN_PCT and at_high:
            flags.append({
                "key": "stall", "name": "高位放量滞涨", "level": RISK,
                "reason": f"近5日量增至 {vol_recent / vol_before:.1f} 倍但仅涨 {gain5:.1f}%，量增价滞多为大资金撤离",
            })

    # 5) 破位：收盘同时跌破 MA10 与 MA20
    if n >= 20 and close > 0:
        ma10 = float(closes.tail(10).mean())
        ma20 = float(closes.tail(20).mean())
        if close < ma10 and close < ma20:
            flags.append({
                "key": "breakdown", "name": "破位下行", "level": RISK,
                "reason": f"收盘 {close:.2f} 已同时跌破 MA10({ma10:.2f}) 与 MA20({ma20:.2f})，趋势转弱勿盲目抄底",
            })

    # 6) 问题股：ST/退市标记 或 业绩预亏预减
    name = str(name or "")
    if "ST" in name.upper() or "退" in name:
        flags.append({"key": "trouble", "name": "问题股", "level": RISK,
                      "reason": "ST/退市风险标记，基本面存在重大不确定性"})
    elif bad_forecast:
        flags.append({"key": "trouble", "name": "问题股", "level": RISK,
                      "reason": "业绩预告预亏/预减，基本面恶化中"})

    risk_count = sum(1 for f in flags if f["level"] == RISK)
    if risk_count >= 2:
        advice = f"回避：命中 {risk_count} 项七不买风险"
    elif risk_count == 1:
        advice = f"谨慎：命中「{next(f['name'] for f in flags if f['level'] == RISK)}」"
    else:
        advice = "未命中七不买风险项"
    return {"flags": flags, "risk_count": risk_count,
            "advice": advice + "（规则化提示，不构成投资建议）"}
