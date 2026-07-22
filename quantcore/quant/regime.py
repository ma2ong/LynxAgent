"""大盘环境温度：逐日广度 → 时间衰减加权温度分 → 环境标签。

engine.market_context 与 replay._session_regimes 共用本模块——口径必须同源，
否则线上标签与回放分层结论互相矛盾。

相对旧口径修正三处：
1. 旧口径用「5 日累计涨幅中位 + 5 日累计上涨占比」，一根 -3% 大阴线能把标签摁住
   整整 5 个交易日，其后连涨也翻不过来。新口径逐日算温度再加权，最新一日占 0.40，
   反弹当天标签就会动。
2. 旧口径「上涨占比」是「5 日累计仍为正的股票比例」，界面上极易被读成「今日上涨家数」，
   同一个 27% 差着一个数量级的含义。逐日口径下这个数就是字面意思。
3. 旧口径判定不对称——偏暖要 `median>=1 AND breadth>=0.55`，偏冷只要
   `median<=-1 OR breadth<=0.40`，于是「中位 +2% 但广度 38%」的分化上涨日会被判偏冷，
   标签系统性偏冷。新口径合成单一温度分，上下对称分档。
"""
from __future__ import annotations

from typing import Sequence, Tuple

# 最新一日权重最高，越往前越轻；不足 5 日按实有天数归一化。
DAY_WEIGHTS: Tuple[float, ...] = (0.40, 0.25, 0.15, 0.12, 0.08)
WARM_TEMP = 60.0
COLD_TEMP = 40.0


def day_temp(median_pct: float, breadth_up: float) -> float:
    """单日温度分（0-100，50 = 中性）。中位涨幅与上涨广度各占一半量纲。

    标定：中位 +1.0% / 广度 60% → 64（偏暖门槛之上）；
          中位 -1.0% / 广度 40% → 36（偏冷门槛之下），与旧阈值语义对齐。
    """
    score = 50.0 + float(median_pct) * 8.0 + (float(breadth_up) - 0.5) * 60.0
    return max(0.0, min(100.0, score))


def blend_temp(days: Sequence[Tuple[float, float]]) -> float:
    """days 为 (median_pct, breadth_up)，**最新一日在前**。返回加权温度分。"""
    picked = list(days)[: len(DAY_WEIGHTS)]
    if not picked:
        return 50.0
    weights = DAY_WEIGHTS[: len(picked)]
    return sum(day_temp(m, b) * w for (m, b), w in zip(picked, weights)) / sum(weights)


def classify(temp: float) -> str:
    """温度分 → 环境标签（对称分档）。"""
    if temp >= WARM_TEMP:
        return "偏暖"
    if temp <= COLD_TEMP:
        return "偏冷"
    return "中性"
