from __future__ import annotations

import importlib.util
import math
import os
import shutil
import sys
from dataclasses import asdict
from datetime import timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from .backtest import run_long_only_backtest
from .factors import enrich_indicators
from .models import ForecastResult, IntegrationCapability, PatternRecognitionResult
from .strategies import STRATEGIES


def _safe_number(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _signed_pct(value: float) -> str:
    return f"{value:+.2f}%"


def _pattern(
    key: str,
    name: str,
    strength: float,
    reason: str,
    evidence: Optional[Dict[str, Any]] = None,
    annotations: Optional[List[Dict[str, Any]]] = None,
    category: str = "",
) -> Dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "active": True,
        "strength": round(max(0.0, min(100.0, strength)), 1),
        "reason": reason,
        "evidence": evidence or {},
        # 分类：""=普通突破/买入形态；"三不卖"=低位持有形态（三军会师/双管齐下/五阳上阵）
        "category": category,
        # 图上特征标注：box(框)/circle(圆圈)/arrow(箭头)/hline(压力线)，date 定位避免错位
        "annotations": annotations or [],
    }


def _with_macd(data: pd.DataFrame) -> pd.DataFrame:
    out = data.copy()
    ema12 = out["close"].ewm(span=12, adjust=False).mean()
    ema26 = out["close"].ewm(span=26, adjust=False).mean()
    out["macd_dif"] = ema12 - ema26
    out["macd_dea"] = out["macd_dif"].ewm(span=9, adjust=False).mean()
    out["macd_hist"] = (out["macd_dif"] - out["macd_dea"]) * 2
    return out


def _detect_pre_lift_patterns(data: pd.DataFrame) -> List[Dict[str, Any]]:
    patterns: List[Dict[str, Any]] = []
    if len(data) < 80:
        return patterns

    data = _with_macd(data)
    latest = data.iloc[-1]
    prev = data.iloc[-2]
    # 图上标注用：按位置取日期；index 标签→位置映射，避免 idxmin 返回标签错位
    _dlist = data["date"].astype(str).str.slice(0, 10).tolist()
    _N = len(data)
    _pos_of = {lbl: i for i, lbl in enumerate(data.index)}

    def _dt(pos: int) -> str:
        return _dlist[pos if pos >= 0 else _N + pos]
    close = _safe_number(latest.get("close"))
    open_ = _safe_number(latest.get("open"))
    high = _safe_number(latest.get("high"))
    volume = _safe_number(latest.get("volume"))
    volume_ma20 = _safe_number(latest.get("volume_ma20"), volume)
    amount = _safe_number(latest.get("amount"))
    pct_chg = (close / _safe_number(prev.get("close"), close) - 1) * 100 if close else 0.0

    ma_window = data.tail(45)
    compressed: List[float] = []
    for _, row in ma_window.iterrows():
        mas = [_safe_number(row.get(col)) for col in ("ma5", "ma10", "ma20", "ma60")]
        if min(mas) <= 0:
            continue
        compressed.append((max(mas) - min(mas)) / _safe_number(row.get("close"), max(mas)))
    min_spread = min(compressed) if compressed else 1.0
    ma5 = _safe_number(latest.get("ma5"))
    ma10 = _safe_number(latest.get("ma10"))
    ma20 = _safe_number(latest.get("ma20"))
    ma60 = _safe_number(latest.get("ma60"))
    ma20_prev = _safe_number(data.iloc[-6].get("ma20"), ma20) if len(data) > 6 else ma20
    if min_spread <= 0.075 and close > ma5 > ma10 > ma20 and close > ma60 and ma20 >= ma20_prev * 0.995:
        patterns.append(_pattern(
            "ma_convergence_expand",
            "均线粘合后向上发散",
            72 + (0.075 - min_spread) * 220 + max(0, pct_chg) * 1.2,
            f"均线曾压缩到 {min_spread * 100:.2f}% 内，当前站上 5/10/20/60 日均线并形成短线多头排列。",
            {"min_ma_spread": round(min_spread, 4), "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60},
            [
                {"type": "box", "date1": _dt(-45), "date2": _dt(-6), "label": "均线粘合"},
                {"type": "arrow", "date": _dt(-1), "price": high, "label": "向上发散"},
            ],
        ))

    recent_20 = data.tail(20)
    dry_volume = _safe_number(recent_20["volume"].min())
    dry_ratio = dry_volume / volume_ma20 if volume_ma20 else 1.0
    prior_high_5 = _safe_number(data.iloc[-6:-1]["high"].max(), high)
    if dry_ratio <= 0.58 and close > open_ and pct_chg >= 1.2 and volume_ma20 > 0 and volume >= volume_ma20 * 1.35 and close >= prior_high_5 * 0.995:
        _dv_pos = (_N - 20) + int(recent_20["volume"].values.argmin())
        patterns.append(_pattern(
            "dry_volume_breakout",
            "地量洗盘后放量阳线",
            70 + (0.58 - dry_ratio) * 28 + min(volume / volume_ma20, 3) * 6 + min(max(pct_chg, 0), 10),
            f"近 20 日出现地量，当前放量 {volume / volume_ma20:.1f} 倍并收阳，涨幅 {_signed_pct(pct_chg)}。",
            {"dry_volume_ratio": round(dry_ratio, 3), "volume_ratio": round(volume / volume_ma20, 2) if volume_ma20 else None},
            [
                {"type": "box", "date1": _dt(max(0, _dv_pos - 3)), "date2": _dt(min(_N - 1, _dv_pos + 2)), "label": "地量区域"},
                {"type": "arrow", "date": _dt(-1), "price": high, "label": "放量阳线"},
            ],
        ))

    # --- 地量点火（2026-09-03）。与上面的「地量洗盘后放量阳线」是两条不同口径：
    # 那条问「近 20 日曾不曾出现过地量」，宽松、且不看位置和板块，从未过审计；
    # 这条是 experiments/rule_audit.py 里 ignite_best 的生产实现，四个条件缺一不可，
    # 板块那一条在 lite_main 层补（这里拿不到横截面）。
    #
    # 审计结论必须连同口径一起记住，否则以后没人知道这些阈值是哪来的：
    #   ig2_best，T+3、T+1 开盘买入口径，样本 6231 笔 / 1213 个交易日，
    #   匹配对照增量 +0.16pp，7 个年份方向一致，但 **CI 下沿 −0.05、去右尾后 −0.02**
    #   —— 也就是跟 0 分不开，七关里倒在「增量显著」和「去右尾」两关。
    # 另有一版分母用 20 日均额的口径（ignite_best）统计上更好（增量 +0.25、CI 下沿 +0.01、
    # 去右尾 +0.05），但它**抓不到欢瑞/竞业达这一类**（分母被前期天量污染，见下），
    # 装上去等于挂着这个名字却选不出这个形态，所以取的是能抓到的这一版。
    # 证据强度：「看着有、统计上分不清是不是挑出来的」。Allen
    # 2026-09-03 拍板先上排序，靠上线后的独立留痕做样本外复判，届时再定去留。
    if len(data) >= 70 and "amount" in data:
        amt = data["amount"].astype(float)
        # 「地量」用自身近 60 日的分位，不用量比：量比的分母会被前期一根天量拉高，
        # 于是放量后的第二天自动成了「地量」（见 rule_audit 里同一处注释）。
        rank60 = amt.rolling(60, min_periods=40).rank(pct=True)
        closes = data["close"].astype(float)

        def _ignition_at(k: int):
            """第 -1-k 根 K 线是不是点火日；是则返回它的四个剂量值。k=0 即当日。"""
            i = len(data) - 1 - k
            if i < 66:
                return None
            quiet = int((rank60.iloc[i - 5:i] <= 0.10).sum())      # 前 5 日，不含该日
            base = float(amt.iloc[i - 5:i].mean())                 # 分母是沉寂期自己
            if base <= 0:
                return None
            ratio = float(amt.iloc[i]) / base
            prev_c = float(closes.iloc[i - 1])
            chg = (float(closes.iloc[i]) / prev_c - 1) * 100 if prev_c else 0.0
            ma20_i = float(closes.iloc[i - 19:i + 1].mean())
            dist = (float(closes.iloc[i]) / ma20_i - 1) * 100 if ma20_i else 999.0
            if quiet >= 2 and ratio >= 2.0 and chg >= 3.0 and dist <= 5.0:
                return {"quiet_days": quiet, "amount_ratio": round(ratio, 2),
                        "pct_chg": round(chg, 2), "dist_ma20": round(dist, 1)}
            return None

        # 往回找最近一次点火。窗口取 3 天（审计有效窗口），第 4 天起不再标注 ——
        # 标一个已经失效的形态比不标更糟，用户会以为它还成立。
        for k in range(0, 4):
            hit = _ignition_at(k)
            if not hit:
                continue
            fresh = k == 0
            _i = len(data) - 1 - k
            patterns.append(_pattern(
                "dryup_ignite",
                "地量点火" if fresh else f"地量点火 D+{k}",
                # 剂量映射到强度：沉寂越久、点火越猛、位置越低分越高；过了点火日逐日衰减。
                (70 + min(hit["quiet_days"], 5) * 3 + min(hit["amount_ratio"], 4.0) * 4
                 + max(0.0, 5.0 - hit["dist_ma20"])) - k * 6,
                (f"前 5 日有 {hit['quiet_days']} 天成交额处在自身 60 日最低 10%（地量），"
                 f"{'今日' if fresh else f'{k} 个交易日前'}成交额放大到沉寂期的 "
                 f"{hit['amount_ratio']:.1f} 倍并收阳 {_signed_pct(hit['pct_chg'])}，"
                 f"距 20 日线 {hit['dist_ma20']:+.1f}%（未追高）。"
                 + ("有效窗口约 3 个交易日。" if fresh
                    else f"已是点火后第 {k} 天，窗口还剩约 {3 - k} 天。")),
                dict(hit, days_since=k, horizon_days=3, fresh=fresh),
                [
                    {"type": "box", "date1": _dt(_i - 5), "date2": _dt(_i - 1), "label": "地量沉寂"},
                    {"type": "arrow", "date": _dt(_i), "price": float(data["high"].iloc[_i]),
                     "label": "点火"},
                ],
            ))
            break

    support = _safe_number(data.iloc[-12:-3]["low"].min())
    recent_dip = data.tail(6).iloc[:-1]
    if support > 0 and not recent_dip.empty:
        dip_low = _safe_number(recent_dip["low"].min())
        dip_break = dip_low < support * 0.985 or dip_low < ma20 * 0.975
        reclaimed = close > max(support, ma20) and pct_chg > 0
        if dip_break and reclaimed:
            depth = (max(support, ma20) / dip_low - 1) * 100 if dip_low else 0
            patterns.append(_pattern(
                "washout_reclaim",
                "挖坑后快速收复",
                68 + min(depth, 8) * 3 + min(max(pct_chg, 0), 8) * 1.5,
                f"短线一度跌破支撑/20日线，当前快速收复关键位，修复幅度约 {depth:.1f}%。",
                {"dip_low": round(dip_low, 3), "support": round(support, 3), "ma20": round(ma20, 3)},
                [
                    {"type": "circle", "date": _dt((_N - 6) + int(recent_dip["low"].values.argmin())), "price": dip_low, "label": "挖坑"},
                    {"type": "hline", "price": round(max(support, ma20), 3), "label": "支撑位"},
                    {"type": "arrow", "date": _dt(-1), "price": close, "label": "快速收复"},
                ],
            ))

    prior_60 = data.iloc[-65:-1]
    if len(prior_60) >= 40:
        pressure = _safe_number(prior_60["high"].max())
        tests = prior_60.tail(35)
        near_tests = tests[(tests["high"] >= pressure * 0.97) & (tests["close"] < pressure * 1.005)]
        if pressure > 0 and len(near_tests) >= 3 and close > pressure * 1.005 and volume_ma20 > 0 and volume >= volume_ma20 * 1.25:
            avg_test_volume = _safe_number(near_tests["volume"].mean())
            shrink_ok = avg_test_volume <= volume_ma20 * 1.25
            patterns.append(_pattern(
                "pressure_test_breakout",
                "压力位试盘后突破",
                72 + min(len(near_tests), 6) * 2.5 + min(volume / volume_ma20, 3) * 5 + (4 if shrink_ok else 0),
                f"近 35 日多次试探 {pressure:.2f} 附近压力，当前放量突破压力位。",
                {"pressure": round(pressure, 3), "test_count": int(len(near_tests)), "volume_ratio": round(volume / volume_ma20, 2)},
                (
                    [{"type": "hline", "price": round(pressure, 3), "label": f"压力位 {pressure:.2f}"}]
                    + [
                        {"type": "circle", "date": _dt(_pos_of[idx]), "price": _safe_number(data.loc[idx, "high"]), "label": ""}
                        for idx in list(near_tests.index)[:6]
                    ]
                    + [{"type": "arrow", "date": _dt(-1), "price": high, "label": "突破压力"}]
                ),
            ))

    macd_recent = data.tail(80)
    low_recent = macd_recent["close"].tail(20).idxmin()
    low_prior = macd_recent["close"].head(60).idxmin()
    price_new_low = _safe_number(data.loc[low_recent, "close"]) <= _safe_number(data.loc[low_prior, "close"]) * 1.02
    dif_higher = _safe_number(data.loc[low_recent, "macd_dif"]) > _safe_number(data.loc[low_prior, "macd_dif"]) * 0.96
    hist_turn_up = _safe_number(latest.get("macd_hist")) > _safe_number(prev.get("macd_hist"))
    golden_cross = _safe_number(prev.get("macd_dif")) <= _safe_number(prev.get("macd_dea")) and _safe_number(latest.get("macd_dif")) > _safe_number(latest.get("macd_dea"))
    air_refuel = _safe_number(latest.get("macd_dif")) > 0 and (golden_cross or (_safe_number(prev.get("macd_hist")) <= 0 < _safe_number(latest.get("macd_hist"))))
    if (price_new_low and dif_higher and hist_turn_up) or air_refuel:
        _macd_ann = [
            {"type": "circle", "date": _dt(_pos_of[low_recent]), "price": _safe_number(data.loc[low_recent, "close"]), "label": "股价低点"},
            {"type": "arrow", "date": _dt(-1), "price": close, "label": "金叉向上" if golden_cross else "空中加油"},
        ]
        # 在 MACD 子图上圈出最近一次 DIF 上穿 DEA 的金叉点
        _dif = data["macd_dif"].values
        _dea = data["macd_dea"].values
        for _j in range(_N - 1, max(_N - 26, 0), -1):
            if _dif[_j - 1] <= _dea[_j - 1] and _dif[_j] > _dea[_j]:
                _macd_ann.append({"type": "circle", "grid": "macd", "date": _dt(_j),
                                  "price": round(float(_dif[_j]), 4), "label": "金叉"})
                break
        patterns.append(_pattern(
            "macd_divergence_acceleration",
            "MACD底背离修复" if price_new_low and dif_higher else "MACD空中加油",
            70 + (8 if golden_cross else 0) + (6 if air_refuel else 0) + min(max(pct_chg, 0), 8),
            "MACD 动能修复，价格与动能背离改善或零轴上方重新金叉。",
            {"dif": round(_safe_number(latest.get("macd_dif")), 4), "dea": round(_safe_number(latest.get("macd_dea")), 4), "hist": round(_safe_number(latest.get("macd_hist")), 4)},
            _macd_ann,
        ))

    run = data.tail(7)
    daily_pct = run["close"].pct_change().fillna(0) * 100
    up_days = int((run["close"] > run["open"]).sum())
    controlled_up = int(((daily_pct > 0.2) & (daily_pct < 4.5)).sum())
    close_rising = _safe_number(run["close"].iloc[-1]) > _safe_number(run["close"].iloc[0]) * 1.04
    volume_rising = _safe_number(run["volume"].tail(3).mean()) > _safe_number(run["volume"].head(3).mean()) * 1.05
    if up_days >= 4 and controlled_up >= 4 and close_rising and volume_rising and pct_chg < 11:
        patterns.append(_pattern(
            "small_step_run",
            "小步快跑",
            68 + up_days * 3 + controlled_up * 2 + min(max(pct_chg, 0), 5),
            f"近 7 日有 {up_days} 根阳线，涨幅温和推进且量能逐步放大。",
            {"up_days": up_days, "controlled_up_days": controlled_up},
            [
                {"type": "box", "date1": _dt(-7), "date2": _dt(-1), "label": "小步快跑(连续小阳线)"},
                {"type": "arrow", "date": _dt(-1), "price": high, "label": "趋势转强"},
            ],
        ))

    box = data.tail(18).iloc[:-1]
    if len(box) >= 12:
        box_high = _safe_number(box["high"].max())
        box_low = _safe_number(box["low"].min())
        box_range = (box_high / box_low - 1) if box_low else 1
        box_vol = _safe_number(box["volume"].mean())
        if box_range <= 0.14 and _safe_number(box["close"].iloc[-1]) >= box_low * 1.04 and close > box_high * 1.01 and box_vol > 0 and volume > box_vol * 1.45:
            patterns.append(_pattern(
                "order_pressure_breakout_proxy",
                "压盘不跌后放量突破",
                69 + (0.14 - box_range) * 80 + min(volume / box_vol, 3) * 6,
                "未接入 Level2 盘口时的代理信号：高位横盘不跌、量能压缩后放量突破箱体。",
                {"box_high": round(box_high, 3), "box_range": round(box_range, 4), "volume_ratio": round(volume / box_vol, 2)},
                [
                    {"type": "box", "date1": _dt(-18), "date2": _dt(-2), "label": "横盘压单"},
                    {"type": "hline", "price": round(box_high, 3), "label": "压单区"},
                    {"type": "arrow", "date": _dt(-1), "price": high, "label": "放量突破"},
                ],
            ))

    # ---- 三不卖（低位持有/介入形态，源自经典口诀，2026-07-17 并入）----
    # 「低位」统一定义：收盘处于近 120 日区间下 40%，避免把高位形态误判成底部信号。
    low_window = data.tail(120)
    lw_low = _safe_number(low_window["low"].min())
    lw_high = _safe_number(low_window["high"].max())
    at_low = lw_high > lw_low > 0 and (close - lw_low) / (lw_high - lw_low) <= 0.40

    # 三军会师：MA5>MA10>MA20 多头排列且三线金叉发生在近 5 日内（低位）
    if at_low and min(ma5, ma10, ma20) > 0 and ma5 > ma10 > ma20:
        five_ago = data.iloc[-6] if len(data) > 6 else data.iloc[0]
        was_bear = _safe_number(five_ago.get("ma5")) <= _safe_number(five_ago.get("ma10")) or \
                   _safe_number(five_ago.get("ma10")) <= _safe_number(five_ago.get("ma20"))
        if was_bear:
            spread = (ma5 / ma20 - 1) * 100
            patterns.append(_pattern(
                "ma_triple_cross_low",
                "三军会师",
                66 + min(spread, 5) * 3 + max(0, pct_chg) * 1.5,
                f"低位 5/10/20 日均线近 5 日内完成金叉并多头排列（MA5 高于 MA20 {spread:.1f}%），趋势向好。",
                {"ma5": ma5, "ma10": ma10, "ma20": ma20, "position_in_120d": round((close - lw_low) / (lw_high - lw_low), 3)},
                [{"type": "arrow", "date": _dt(-1), "price": high, "label": "三线金叉"}],
                category="三不卖",
            ))

    # 双管齐下：近 3 根内两根长下影小实体 K 线、下影最低点相近（低位）
    if at_low and len(data) >= 3:
        tail3 = data.tail(3)
        hammers = []
        for pos_off, (_, row) in enumerate(tail3.iterrows()):
            o, c, lo = _safe_number(row.get("open")), _safe_number(row.get("close")), _safe_number(row.get("low"))
            body = abs(c - o)
            lower_shadow = min(o, c) - lo
            if c > 0 and lower_shadow >= max(body, c * 0.002) * 2 and body / c < 0.015:
                hammers.append((pos_off, lo))
        if len(hammers) >= 2:
            lows = [h[1] for h in hammers[-2:]]
            if min(lows) > 0 and abs(lows[0] - lows[1]) / min(lows) < 0.015:
                patterns.append(_pattern(
                    "double_hammer_low",
                    "双管齐下",
                    64 + max(0, pct_chg) * 2,
                    f"低位连续出现两根长下影小实体 K 线，下影最低点相近（{min(lows):.2f}），下方承接强劲。",
                    {"hammer_lows": [round(v, 3) for v in lows]},
                    [{"type": "hline", "price": round(min(lows), 3), "label": "下影支撑"}],
                    category="三不卖",
                ))

    # 五阳上阵：连续 5 日小阳线、累计涨幅温和（低位）
    if at_low and len(data) >= 5:
        tail5 = data.tail(5)
        opens5 = tail5["open"].astype(float)
        closes5 = tail5["close"].astype(float)
        all_bull = bool((closes5.values > opens5.values).all())
        small_bodies = bool(((closes5.values - opens5.values) / opens5.values <= 0.03).all()) if all_bull else False
        cum_gain = (float(closes5.iloc[-1]) / float(opens5.iloc[0]) - 1) * 100 if float(opens5.iloc[0]) > 0 else 99
        if all_bull and small_bodies and cum_gain < 12:
            patterns.append(_pattern(
                "five_soldiers_low",
                "五阳上阵",
                63 + cum_gain * 1.2,
                f"低位连续 5 日收小阳（累计 {_signed_pct(cum_gain)}），多方稳步掌控局面。",
                {"cum_gain_pct": round(cum_gain, 2)},
                [{"type": "box", "date1": _dt(-5), "date2": _dt(-1), "label": "五连阳"}],
                category="三不卖",
            ))

    patterns.sort(key=lambda item: item["strength"], reverse=True)
    return patterns


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _tool_available(name: str) -> bool:
    return shutil.which(name) is not None


def integration_capabilities() -> List[Dict[str, Any]]:
    capabilities = [
        IntegrationCapability(
            name="akshare",
            project="akfamily/akshare",
            status="active" if _module_available("akshare") else "optional_missing",
            capabilities=["A股行情", "历史K线", "股票列表", "财务/宏观/公告数据入口"],
            integration_mode="primary_data_source",
            notes="已作为量化数据层的优先数据源使用。",
        ),
        IntegrationCapability(
            name="akquant",
            project="akfamily/akquant",
            status="optional_available" if _module_available("akquant") else "adapter_fallback",
            capabilities=["高性能回测", "策略评估", "参数优化"],
            integration_mode="optional_engine",
            notes="未安装时自动使用本项目 vector 回测引擎，接口保持一致。",
        ),
        IntegrationCapability(
            name="sequoia_x_patterns",
            project="sngyai/Sequoia-X",
            status="active",
            capabilities=["RPS突破", "海龟突破", "均线放量", "高窄旗形", "涨停洗盘"],
            integration_mode="strategy_templates",
            notes="按项目思想重写为本地确定性策略，避免直接复制代码。",
        ),
        IntegrationCapability(
            name="backtrader",
            project="mementum/backtrader",
            status="optional_available" if _module_available("backtrader") else "optional_missing",
            capabilities=["事件驱动回测", "订单撮合模拟", "策略执行校验"],
            integration_mode="optional_backtest_adapter",
            notes="考虑 GPL-3.0 许可，作为可选适配器，不作为核心强依赖。",
        ),
        IntegrationCapability(
            name="quantgpt_factor_research",
            project="Miasyster/QuantGPT",
            status="active",
            capabilities=["因子假设生成", "因子回测", "候选因子评分", "反过拟合前置筛选"],
            integration_mode="deterministic_factor_agent",
            notes="采用确定性白名单因子模板，LLM 只做研究辅助，不直接执行任意代码。",
        ),
        IntegrationCapability(
            name="wyckoff_vsa_cleanroom",
            project="YoungCan-Wang/WyckoffTradingAgent",
            status="active_cleanroom",
            capabilities=["Wyckoff/VSA 阶段判断", "量价努力-结果分析", "Spring/Upthrust 风险识别", "形态池评分修正"],
            integration_mode="clean_room_volume_price_analysis",
            notes="AGPL-3.0 项目仅作功能启发；未复制源码，按 Wyckoff/VSA 公共交易概念重写。",
        ),
        IntegrationCapability(
            name="ml_feature_engineering_cleanroom",
            project="stefan-jansen/machine-learning-for-trading",
            status="active_cleanroom",
            capabilities=["趋势持续性", "风险调整动量", "波动分位", "流动性质量", "回撤修复"],
            integration_mode="clean_room_feature_summary",
            notes="该仓库未声明清晰许可；仅借鉴 ML 交易特征工程思路，未复制 notebook 或源码。",
        ),
        IntegrationCapability(
            name="easyquant_strategy_validation",
            project="HiRenyi/EasyQuant",
            status="already_covered",
            capabilities=["因子研究循环", "策略验证思路", "批量策略筛选", "回测结果复核"],
            integration_mode="deterministic_factor_agent_and_composable_strategies",
            notes="MIT 项目可借鉴；当前 AStockPick 已有因子 Agent 与组合策略框架，暂不引入聚宽自动化依赖。",
        ),
        IntegrationCapability(
            name="stock_analysis_deep_report",
            project="mingli30119/stock-analysis",
            status="active",
            capabilities=["8步深度个股分析", "产业链", "质量评分", "估值/风险/跟踪计划"],
            integration_mode="deep_analysis_framework",
            notes="已接入单股分析专业报告生成链路。",
        ),
        IntegrationCapability(
            name="kronos",
            project="shiyu-coder/Kronos",
            status="optional_available" if os.getenv("KRONOS_REPO_PATH") or _module_available("model") else "adapter_fallback",
            capabilities=["K线基础模型预测", "未来路径模拟", "趋势概率因子"],
            integration_mode="optional_forecast_engine",
            notes="未配置 Kronos 模型时使用轻量趋势预测适配器，避免本地 CPU/GPU 被重型模型拖慢。",
        ),
        IntegrationCapability(
            name="vibe_trading",
            project="HKUDS/Vibe-Trading",
            status="optional_available" if _tool_available("vibe-trading") else "adapter_fallback",
            capabilities=["自然语言策略", "因子分析", "形态识别", "交易日志", "影子账户", "Swarm 工作流"],
            integration_mode="optional_tool_bridge",
            notes="当前暴露能力入口；检测到 vibe-trading CLI/MCP 后可切换真实工具调用。",
        ),
    ]
    return [asdict(item) for item in capabilities]


def recognize_patterns(symbol: str, df: pd.DataFrame) -> PatternRecognitionResult:
    data = enrich_indicators(df)
    patterns: List[Dict[str, Any]] = []
    if data.empty:
        return PatternRecognitionResult(symbol=symbol, patterns=patterns)

    latest = data.iloc[-1]
    strategy_labels = {
        "turtle_breakout": "海龟突破",
        "rps_breakout": "RPS相对强度突破",
        "ma_volume": "均线放量",
        "high_tight_flag": "高窄旗形",
        "limit_up_washout": "涨停洗盘后修复",
        "multi_ma_breakout": "多均线共振突破",
    }
    for key, signal_func in STRATEGIES.items():
        try:
            signal = signal_func(data).fillna(False)
            active = bool(signal.iloc[-1]) if len(signal) else False
            strength = 75.0 if active else 0.0
            if key in {"rps_breakout", "multi_ma_breakout"}:
                strength += min(max(float(latest.get("momentum_60", 0)) * 80, 0), 20)
            if active:
                patterns.append({
                    "key": key,
                    "name": strategy_labels.get(key, key),
                    "active": True,
                    "strength": round(min(strength, 100), 1),
                    "reason": "最新K线满足该策略的趋势、量能或突破条件。",
                })
        except Exception as exc:
            patterns.append({
                "key": key,
                "name": strategy_labels.get(key, key),
                "active": False,
                "strength": 0,
                "reason": f"形态计算失败: {exc}",
            })

    patterns.sort(key=lambda item: item["strength"], reverse=True)
    return PatternRecognitionResult(symbol=symbol, patterns=patterns)


def recognize_patterns(symbol: str, df: pd.DataFrame) -> PatternRecognitionResult:
    data = enrich_indicators(df)
    patterns: List[Dict[str, Any]] = []
    if data.empty:
        return PatternRecognitionResult(symbol=symbol, patterns=patterns)

    patterns.extend(_detect_pre_lift_patterns(data))
    active_keys = {item["key"] for item in patterns}
    latest = data.iloc[-1]
    strategy_labels = {
        "turtle_breakout": "海龟突破",
        "rps_breakout": "RPS相对强度突破",
        "ma_volume": "均线放量",
        "high_tight_flag": "高紧旗形",
        "limit_up_washout": "涨停洗盘后修复",
        "multi_ma_breakout": "多均线共振突破",
    }
    for key, signal_func in STRATEGIES.items():
        if key in active_keys:
            continue
        try:
            signal = signal_func(data).fillna(False)
            active = bool(signal.iloc[-1]) if len(signal) else False
            strength = 75.0 if active else 0.0
            if key in {"rps_breakout", "multi_ma_breakout"}:
                strength += min(max(float(latest.get("momentum_60", 0)) * 80, 0), 20)
            if active:
                patterns.append({
                    "key": key,
                    "name": strategy_labels.get(key, key),
                    "active": True,
                    "strength": round(min(strength, 100), 1),
                    "reason": "最新K线满足该策略的趋势、量能或突破条件。",
                    "evidence": {},
                })
        except Exception as exc:
            patterns.append({
                "key": key,
                "name": strategy_labels.get(key, key),
                "active": False,
                "strength": 0,
                "reason": f"形态计算失败: {exc}",
                "evidence": {},
            })

    patterns.sort(key=lambda item: item["strength"], reverse=True)
    return PatternRecognitionResult(symbol=symbol, patterns=patterns)


def kronos_style_forecast(symbol: str, df: pd.DataFrame, horizon: int = 10) -> ForecastResult:
    data = enrich_indicators(df)
    warnings: List[str] = []
    if data.empty:
        raise ValueError(f"{symbol} 没有可预测的K线数据")
    if len(data) < 80:
        warnings.append("历史K线不足 80 根，预测可信度下降")

    latest = data.iloc[-1]
    close = float(latest["close"])
    momentum = float(latest.get("momentum_20") or 0)
    trend = 0.0
    ma20 = float(latest.get("ma20") or close)
    ma60 = float(latest.get("ma60") or close)
    if ma20 and ma60:
        trend = (close / ma20 - 1) * 0.45 + (ma20 / ma60 - 1) * 0.55
    volatility = float(latest.get("volatility20") or 0.35)
    drift = max(min(momentum * 0.35 + trend * 0.65, 0.08), -0.08)
    expected_return = drift * math.sqrt(max(horizon, 1) / 10)
    expected_drawdown = -abs(volatility) * math.sqrt(max(horizon, 1) / 252) * 1.25
    trend_score = max(0.0, min(100.0, 50 + expected_return * 420 - abs(expected_drawdown) * 80))
    upside_probability = max(0.05, min(0.95, 0.5 + expected_return * 3.2 - abs(expected_drawdown) * 0.8))

    last_date = pd.to_datetime(latest["date"], errors="coerce")
    path: List[Dict[str, Any]] = []
    for idx in range(1, horizon + 1):
        progress = idx / horizon
        projected = close * (1 + expected_return * progress)
        band = abs(expected_drawdown) * close * math.sqrt(progress)
        forecast_date = last_date + timedelta(days=idx) if not pd.isna(last_date) else None
        path.append({
            "date": forecast_date.strftime("%Y-%m-%d") if forecast_date is not None else str(idx),
            "close": round(projected, 3),
            "low": round(max(projected - band, 0), 3),
            "high": round(projected + band, 3),
        })

    return ForecastResult(
        symbol=symbol,
        engine="kronos_adapter_fallback",
        horizon=horizon,
        trend_score=round(trend_score, 2),
        upside_probability=round(upside_probability, 4),
        expected_return=round(expected_return, 4),
        expected_drawdown=round(expected_drawdown, 4),
        path=path,
        warnings=warnings,
    )


def run_akquant_backtest_adapter(
    symbol: str,
    df: pd.DataFrame,
    strategy: str,
    initial_cash: float,
) -> Dict[str, Any]:
    signal_func = STRATEGIES[strategy]
    result = run_long_only_backtest(symbol, df, signal_func, strategy, initial_cash)
    if _module_available("akquant"):
        result.engine = "akquant_adapter"
    else:
        result.engine = "vector_fallback_akquant_missing"
    return asdict(result)
