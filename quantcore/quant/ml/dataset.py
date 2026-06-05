"""截面面板数据集构建：OHLCV → 平稳特征矩阵 + 前瞻收益标签。

设计要点（对齐 qlib 方法论，但用本项目的数据/工具实现）：
- 特征全部做成**平稳量**（比率/动量/振荡指标），剔除原始价格水平，避免跨股不可比。
- 标签为**前瞻 N 日收益** label = close[t+N]/close[t] - 1，训练时只能用 t 时刻已知的特征预测它。
- 截面归一化：每个交易日内对每个特征做 rank 归一（CSRankNorm 风格），消除个股量纲与市场漂移。
- 严格防未来函数：特征只用历史滚动窗口，标签用 shift(-N)，组装后丢掉最后 N 行。
"""
from __future__ import annotations

from typing import List, Optional, Sequence

import numpy as np
import pandas as pd

from ..factors import enrich_indicators
from ..local_store import get_local_store

LABEL_COL = "label_fwd"
DATE_COL = "date"
SYMBOL_COL = "symbol"
SIZE_COL = "size"  # 市值代理（成交额对数），仅用于中性化，不进模型

# 最终进模型的平稳特征列（全部由 build_features 生成）
FEATURE_COLS: List[str] = [
    "ret",
    "momentum_5", "momentum_10", "momentum_20", "momentum_60", "momentum_120",
    "volatility20", "drawdown",
    "rsi14", "kdj_k", "kdj_d", "kdj_j",
    "adx14", "plus_di", "minus_di",
    "cci20", "williams_r", "stochrsi_k", "mfi14", "cmf20",
    "close_ma5", "close_ma20", "close_ma60", "close_ma120",
    "ma5_ma20", "ma20_ma60",
    "bb_pos", "kc_pos",
    "macd_norm", "macd_hist", "atr_norm",
    "vol_ratio", "amt_ratio",
    # --- Alpha158 风格扩展因子 ---
    "kmid", "klen", "kup", "klow",            # K 线形态
    "roc_30", "ret_std_5", "ret_std_10", "ret_std_30", "ret_std_60",  # 多周期波动
    "ret_max20", "ret_min20", "ret_skew20",   # 收益分布
    "up_cnt20", "pos_60",                      # 上涨占比 / 区间位置
    "cv_corr20", "vol_std20",                  # 量价相关 / 量能波动
]


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """单只股票 OHLCV → 平稳特征。输入需含 date/open/high/low/close/volume/amount。"""
    d = enrich_indicators(df)
    close = d["close"].replace(0, np.nan)

    out = pd.DataFrame(index=d.index)
    out[DATE_COL] = df[DATE_COL].values if DATE_COL in df.columns else d.index

    # 直接平稳的指标
    for col in ("ret", "momentum_5", "momentum_10", "momentum_20", "momentum_60",
                "momentum_120", "volatility20", "drawdown", "rsi14", "kdj_k",
                "kdj_d", "kdj_j", "adx14", "plus_di", "minus_di", "cci20",
                "williams_r", "stochrsi_k", "mfi14", "cmf20"):
        out[col] = d[col]

    # 价格相对均线（比率化）
    out["close_ma5"] = close / d["ma5"] - 1
    out["close_ma20"] = close / d["ma20"] - 1
    out["close_ma60"] = close / d["ma60"] - 1
    out["close_ma120"] = close / d["ma120"] - 1
    out["ma5_ma20"] = d["ma5"] / d["ma20"] - 1
    out["ma20_ma60"] = d["ma20"] / d["ma60"] - 1

    # 通道内相对位置
    bb_width = (d["bb_upper"] - d["bb_lower"]).replace(0, np.nan)
    out["bb_pos"] = (d["close"] - (d["bb_upper"] + d["bb_lower"]) / 2) / bb_width
    kc_width = (d["kc_upper"] - d["kc_lower"]).replace(0, np.nan)
    out["kc_pos"] = (d["close"] - d["kc_mid"]) / kc_width

    # 价格归一化的趋势/波动
    out["macd_norm"] = d["macd_line"] / close
    out["macd_hist"] = (d["macd_line"] - d["macd_signal"]) / close
    out["atr_norm"] = d["atr14"] / close

    # 量能比率
    out["vol_ratio"] = d["volume"] / d["volume_ma20"].replace(0, np.nan) - 1
    out["amt_ratio"] = d["amount"] / d["amount_ma20"].replace(0, np.nan) - 1

    # --- Alpha158 风格扩展因子（全部由 OHLCV 推导，保持平稳）---
    o, h, l, c = d["open"], d["high"], d["low"], d["close"]
    hl = (h - l).replace(0, np.nan)
    out["kmid"] = (c - o) / hl                       # 实体相对振幅
    out["klen"] = hl / o.replace(0, np.nan)          # 当日振幅
    out["kup"] = (h - np.maximum(o, c)) / hl         # 上影线
    out["klow"] = (np.minimum(o, c) - l) / hl        # 下影线

    ret = d["ret"]
    out["roc_30"] = close.pct_change(30)
    out["ret_std_5"] = ret.rolling(5).std()
    out["ret_std_10"] = ret.rolling(10).std()
    out["ret_std_30"] = ret.rolling(30).std()
    out["ret_std_60"] = ret.rolling(60).std()
    out["ret_max20"] = ret.rolling(20).max()
    out["ret_min20"] = ret.rolling(20).min()
    out["ret_skew20"] = ret.rolling(20).skew()
    out["up_cnt20"] = (ret > 0).rolling(20).mean()   # 20 日上涨占比
    low60, high60 = l.rolling(60).min(), h.rolling(60).max()
    out["pos_60"] = (c - low60) / (high60 - low60).replace(0, np.nan)  # 60 日区间位置
    out["cv_corr20"] = c.rolling(20).corr(d["volume"])                # 量价相关
    out["vol_std20"] = d["volume"].pct_change().rolling(20).std()     # 量能波动

    # 市值代理（成交额 20 日均值取对数）——仅供中性化使用
    out[SIZE_COL] = np.log1p(d["amount_ma20"])

    # 可投资域过滤用的原始量（不进模型）
    out["_close"] = d["close"]          # 当日收盘价（剔除仙股）
    out["_amt20"] = d["amount_ma20"]    # 20 日均成交额（剔除低流动性）

    return out


def _add_label(feat: pd.DataFrame, df: pd.DataFrame, horizon: int) -> pd.DataFrame:
    close = df["close"].reset_index(drop=True)
    fwd = close.shift(-horizon) / close - 1
    feat = feat.reset_index(drop=True)
    feat[LABEL_COL] = fwd.values
    return feat


def _excluded_symbols(store, exclude_st: bool, exclude_delisting: bool) -> set:
    """按名称剔除不可投资的代码：ST/*ST（风险警示）、退（退市/退市整理期）。"""
    if not (exclude_st or exclude_delisting):
        return set()
    excluded = set()
    try:
        for r in store.load_meta():
            name = (r.get("name") or "")
            sym = r.get("symbol")
            if exclude_st and "ST" in name.upper():
                excluded.add(sym)
            if exclude_delisting and "退" in name:
                excluded.add(sym)
    except Exception:
        pass
    return excluded


def build_panel(
    symbols: Optional[Sequence[str]] = None,
    horizon: int = 5,
    min_rows: int = 180,
    start_date: Optional[str] = None,
    cs_rank_norm: bool = True,
    exclude_st: bool = True,
    exclude_delisting: bool = True,
    price_floor: float = 2.0,
    min_amount: float = 2e7,
) -> pd.DataFrame:
    """组装全市场截面面板（仅可投资域）。

    返回 DataFrame，列 = [date, symbol] + FEATURE_COLS + [label_fwd]，已按 date 排序。
    可投资域过滤（A 股量化标配，避免选到退市/ST/仙股/极低流动性的不可交易标的）：
      - exclude_st / exclude_delisting：按名称剔除 ST/*ST、退市股（整只剔除）
      - price_floor：剔除当日收盘价 < price_floor 元的仙股（按交易日逐行，点位正确）
      - min_amount：剔除 20 日均成交额 < min_amount 元的低流动性标的
    过滤在截面 rank 归一【之前】完成，保证排序/选股/回测都只在可投资域内。
    """
    store = get_local_store()
    if symbols is None:
        symbols = store.list_kline_symbols(min_rows=min_rows)

    skip = _excluded_symbols(store, exclude_st, exclude_delisting)

    frames: List[pd.DataFrame] = []
    for sym in symbols:
        if sym in skip:
            continue
        raw = store.load_kline(sym)
        if raw is None or len(raw) < min_rows:
            continue
        if start_date:
            raw = raw[raw[DATE_COL] >= start_date]
            if len(raw) < horizon + 30:
                continue
        feat = build_features(raw)
        feat = _add_label(feat, raw, horizon)
        feat[SYMBOL_COL] = sym
        frames.append(feat)

    if not frames:
        return pd.DataFrame(columns=[DATE_COL, SYMBOL_COL] + FEATURE_COLS + [LABEL_COL])

    panel = pd.concat(frames, ignore_index=True)
    panel = panel.replace([np.inf, -np.inf], np.nan)
    # 丢掉特征/标签/市值缺失的行（含每只票末尾 horizon 行）
    panel = panel.dropna(subset=FEATURE_COLS + [SIZE_COL, LABEL_COL])

    # 逐行可投资域过滤（点位正确：用当日价格/流动性，不引入未来信息）
    if price_floor and price_floor > 0:
        panel = panel[panel["_close"] >= price_floor]
    if min_amount and min_amount > 0:
        panel = panel[panel["_amt20"] >= min_amount]

    panel = panel.sort_values([DATE_COL, SYMBOL_COL]).reset_index(drop=True)

    if cs_rank_norm:
        g = panel.groupby(DATE_COL)
        # 特征 + 市值都按交易日做截面 rank 归一到 (0,1]，对异常值稳健
        for col in FEATURE_COLS + [SIZE_COL]:
            panel[col] = g[col].rank(pct=True)

    return panel


def split_by_time(panel: pd.DataFrame, train_end: str, valid_end: str):
    """按交易日切分 train/valid/test，杜绝未来信息泄漏。"""
    train = panel[panel[DATE_COL] <= train_end]
    valid = panel[(panel[DATE_COL] > train_end) & (panel[DATE_COL] <= valid_end)]
    test = panel[panel[DATE_COL] > valid_end]
    return train, valid, test
