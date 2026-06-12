"""大盘情绪/市场宽度统计：全部基于本地日线，无需 LLM 或外部接口。

指标定义（A 股情绪复盘常用口径，均为公开算法）：
- 涨停/跌停：按板块涨跌幅阈值近似（主板≈10%，创业板/科创板≈20%）。
- 连板高度：个股连续涨停天数；梯队按 3 板 / 4 板 / 5 板+ 统计。
- 5 日线占比：收盘价 > MA5 的个股占比（全市场 + 分板块）。
- 涨跌比：上涨家数 / 下跌家数。
- 情绪温度：5日线占比、涨跌比、涨跌停强弱的合成分（0-100）。
- 板块涨跌幅：按代码前缀分段（全A/沪主板/深主板/创业板/科创板）的等权累计收益。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

from .local_store import get_local_store


def _segment(symbol: str) -> str:
    s = str(symbol).zfill(6)
    if s.startswith("688"):
        return "科创板"
    if s.startswith("30"):
        return "创业板"
    if s.startswith("6"):
        return "沪主板"
    return "深主板"


def _limit_threshold(symbol: str) -> float:
    s = str(symbol).zfill(6)
    return 0.195 if s.startswith(("30", "688")) else 0.097


def _cap_band(symbol: str) -> str:
    # 无市值数据，用板块作为大/中/小盘的近似分层
    seg = _segment(symbol)
    if seg in ("沪主板", "深主板"):
        return "大盘股"
    if seg == "创业板":
        return "中盘股"
    return "小盘股"


def _limit_cause(name: str, industry: str, segment: str) -> str:
    text = f"{name}{industry}"
    # 优先查动态概念板块缓存（concept_lookup 按需构建，日缓存）
    try:
        from .concept_lookup import get_concept
        cached = get_concept(name)
        if cached:
            return cached
    except Exception:
        pass
    rules = [
        ("光通信/CPO", ("光模块", "CPO", "光通信", "光电", "光缆", "光纤", "通信设备")),
        ("AI硬件", ("算力", "服务器", "数据中心", "PCB", "铜缆", "连接器", "液冷", "AIPC", "消费电子")),
        ("国产芯片", ("半导体", "芯片", "集成电路", "电子元件", "存储", "封测", "光刻胶")),
        ("机器人", ("机器人", "自动化", "电气设备", "专用设备")),
        ("煤炭", ("煤炭", "煤电", "焦煤", "焦炭")),
        ("电力", ("电力", "能源", "火电", "水电", "核电", "光伏", "风电", "特高压", "变压器", "电网", "输变电")),
        ("航天军工", ("航天", "航空", "军工", "无人机", "卫星", "红外", "飞机", "直升机", "航发", "战斗机")),
        ("有色金属", ("黄金", "铜业", "铜箔", "锂业", "钨业", "稀土", "铝业", "钛合金", "钼", "锗", "锡业", "有色")),
        ("新能源车", ("新能源车", "动力电池", "充电桩", "锂电", "电动车")),
        ("医药", ("创新药", "医疗器械", "CXO", "体外诊断", "生物制药", "医药")),
        ("大消费", ("消费", "食品", "服装", "家电", "日化", "零售")),
        ("公告重组", ("重组", "并购", "资产注入", "中标", "订单", "增持")),
    ]
    for label, keys in rules:
        if any(key in text for key in keys):
            return label
    return "其他"


def _append_realtime_rows(df: pd.DataFrame, end: str, realtime_quotes: Optional[Dict[str, Dict[str, object]]]) -> pd.DataFrame:
    if not realtime_quotes:
        return df
    today = date.today().strftime("%Y-%m-%d")
    if end < today:
        return df
    rows = []
    for symbol, quote in realtime_quotes.items():
        price = quote.get("price") or quote.get("close") or quote.get("current_price")
        if price is None or float(price or 0) <= 0:
            continue
        rows.append(
            {
                "symbol": str(symbol).zfill(6),
                "date": today,
                "close": float(price),
                "amount": float(quote.get("amount") or 0),
            }
        )
    if not rows:
        return df
    symbols = {row["symbol"] for row in rows}
    df = df[~((df["date"] == today) & (df["symbol"].astype(str).str.zfill(6).isin(symbols)))].copy()
    return pd.concat([df, pd.DataFrame(rows)], ignore_index=True)


def compute_market_sentiment(start: str, end: str, buffer_days: int = 24, realtime_quotes: Optional[Dict[str, Dict[str, object]]] = None) -> Dict[str, object]:
    store = get_local_store()
    conn = store._conn()
    win_start = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=buffer_days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT symbol, date, close, amount FROM daily_kline WHERE date >= ? AND date <= ? ORDER BY symbol, date",
        (win_start, end),
    ).fetchall()
    if not rows:
        return {"empty": True, "message": "本地行情为空，请先在「数据同步」做全量同步", "dates": []}

    df = pd.DataFrame(rows, columns=["symbol", "date", "close", "amount"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    df = df.dropna(subset=["close"])
    df = df[df["close"] > 0]
    df = _append_realtime_rows(df, end, realtime_quotes)
    # 修正同步层单位不一致：科创板(688)源的 volume 为「股」却仍按「手」×100，
    # 使其 amount 被放大 100 倍（实测 688 板块单只均值 ~680 亿 vs 主板 ~5 亿）。
    # 对 688 回退 100 倍后，全市场成交额回到 ~2-3 万亿的合理量级。
    # 注：根因在 sync_service 的成交额计算，待修复 + 重新同步后应移除此补偿。
    mask_688 = df["symbol"].astype(str).str.startswith("688")
    df.loc[mask_688, "amount"] = df.loc[mask_688, "amount"] / 100.0

    g = df.groupby("symbol", sort=False)
    df["pct"] = g["close"].pct_change()
    df["ma5"] = g["close"].transform(lambda s: s.rolling(5).mean())
    df["above_ma5"] = df["close"] > df["ma5"]
    thr = df["symbol"].map(_limit_threshold)
    df["limit_up"] = df["pct"] >= thr
    df["limit_down"] = df["pct"] <= -thr
    # 连板高度：连续涨停天数
    grp = (~df["limit_up"]).groupby(df["symbol"]).cumsum()
    df["board_height"] = df["limit_up"].groupby([df["symbol"], grp]).cumsum().where(df["limit_up"], 0).astype(int)
    df["seg"] = df["symbol"].map(_segment)
    df["cap"] = df["symbol"].map(_cap_band)
    meta_rows = conn.execute("SELECT symbol, name, industry FROM stock_meta").fetchall()
    meta = {str(s).zfill(6): {"name": n or "", "industry": i or ""} for s, n, i in meta_rows}
    df["name"] = df["symbol"].map(lambda s: meta.get(str(s).zfill(6), {}).get("name", ""))
    df["industry"] = df["symbol"].map(lambda s: meta.get(str(s).zfill(6), {}).get("industry", ""))
    df["limit_cause"] = df.apply(
        lambda row: _limit_cause(str(row.get("name") or ""), str(row.get("industry") or ""), str(row.get("seg") or "")),
        axis=1,
    )

    # 仅保留展示窗口
    win = df[(df["date"] >= start) & (df["date"] <= end)].copy()
    # 剔除不完整交易日（个股数明显少于中位数，多为未收盘的当日）
    counts = win.groupby("date")["symbol"].size()
    if len(counts):
        threshold_n = counts.median() * 0.4
        complete = set(counts[counts >= threshold_n].index)
        win = win[win["date"].isin(complete)].copy()
    dates = sorted(win["date"].unique().tolist())

    def by_date(series_bool):
        return [int(v) for v in win.groupby("date").apply(lambda x: int(series_bool(x))).reindex(dates).fillna(0)]

    # 逐日聚合
    daily = win.groupby("date")

    # 成交额归一化：Tencent API 仅返回约 2740 只股票（vs 历史 5000+ 只），
    # 导致图表出现人为断崖。用 80 百分位覆盖度作参考，对低覆盖日期按比例放大，
    # 今日盘中数据（覆盖度可能极低）不参与缩放，避免异常放大。
    today_str = date.today().strftime("%Y-%m-%d")
    ref_count = float(counts.quantile(0.8)) if len(counts) else 1.0
    amount_raw = daily["amount"].sum().reindex(dates).fillna(0)
    amount_scaled = amount_raw.copy().astype(float)
    for d in dates:
        cnt = int(counts.get(d, ref_count))
        # 仅对覆盖度在 30%~80% 区间的完整交易日做放大（不含今日盘中）
        if d != today_str and 0 < cnt < ref_count * 0.8:
            amount_scaled[d] = float(amount_raw[d]) * (ref_count / cnt)
    amount_yi = [round(float(v) / 1e8, 1) for v in amount_scaled]
    limit_up_cnt = [int(v) for v in daily["limit_up"].sum().reindex(dates).fillna(0)]
    limit_down_cnt = [int(v) for v in daily["limit_down"].sum().reindex(dates).fillna(0)]
    adv = daily.apply(lambda x: int((x["pct"] > 0).sum())).reindex(dates).fillna(0)
    dec = daily.apply(lambda x: int((x["pct"] < 0).sum())).reindex(dates).fillna(0)
    adv_list = [int(v) for v in adv]
    dec_list = [int(v) for v in dec]

    # 5日线占比（全市场 + 大/中/小盘）
    def above_ratio(frame):
        return round(float(frame["above_ma5"].mean()) * 100, 1) if len(frame) else 0.0
    above_all = [above_ratio(daily.get_group(d)) for d in dates]
    above_cap: Dict[str, List[float]] = {}
    for cap in ("大盘股", "中盘股", "小盘股"):
        sub = win[win["cap"] == cap].groupby("date")
        above_cap[cap] = [above_ratio(sub.get_group(d)) if d in sub.groups else 0.0 for d in dates]

    # 连板梯队
    def board_count(frame, h):
        if h == "5+":
            return int((frame["board_height"] >= 5).sum())
        return int((frame["board_height"] == int(h)).sum())
    boards3 = [board_count(daily.get_group(d), "3") for d in dates]
    boards4 = [board_count(daily.get_group(d), "4") for d in dates]
    boards5 = [board_count(daily.get_group(d), "5+") for d in dates]
    max_height = [int(daily.get_group(d)["board_height"].max() or 0) for d in dates]
    latest_limit = daily.get_group(dates[-1])
    latest_limit = latest_limit[latest_limit["limit_up"]].copy()
    cause_ladder = []
    if not latest_limit.empty:
        for cause, frame in latest_limit.groupby("limit_cause"):
            stocks = frame.sort_values(["board_height", "amount"], ascending=False).head(8)
            cause_ladder.append({
                "cause": cause,
                "total": int(len(frame)),
                "b1": int((frame["board_height"] == 1).sum()),
                "b2": int((frame["board_height"] == 2).sum()),
                "b3": int((frame["board_height"] == 3).sum()),
                "b4": int((frame["board_height"] == 4).sum()),
                "b5plus": int((frame["board_height"] >= 5).sum()),
                "max_height": int(frame["board_height"].max() or 0),
                "stocks": [
                    {
                        "symbol": str(row.symbol).zfill(6),
                        "name": str(row.name or row.symbol),
                        "height": int(row.board_height),
                    }
                    for row in stocks.itertuples(index=False)
                ],
            })
        cause_ladder.sort(key=lambda item: (item["max_height"], item["total"]), reverse=True)

    # 板块等权累计涨跌幅
    seg_returns: Dict[str, List[float]] = {}
    for seg in ["全A", "沪主板", "深主板", "创业板", "科创板"]:
        sub = win if seg == "全A" else win[win["seg"] == seg]
        mean_pct = sub.groupby("date")["pct"].mean().reindex(dates).fillna(0.0)
        cum = (1 + mean_pct).cumprod() - 1
        seg_returns[seg] = [round(float(v) * 100, 2) for v in cum]

    # KPI（最新交易日）
    last = dates[-1]
    prev = dates[-2] if len(dates) >= 2 else None
    turnover_today = amount_yi[-1]
    turnover_prev = amount_yi[-2] if len(amount_yi) >= 2 else turnover_today
    adv_dec_ratio = round(adv_list[-1] / dec_list[-1], 2) if dec_list[-1] else float(adv_list[-1])
    sentiment_temp = _sentiment_temperature(above_all[-1], adv_dec_ratio, limit_up_cnt[-1], limit_down_cnt[-1])

    return {
        "empty": False,
        "start": start, "end": end, "dates": dates, "as_of": last,
        "as_of_time": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "realtime": bool(realtime_quotes and last == date.today().strftime("%Y-%m-%d")),
        "kpi": {
            "sentiment_temperature": sentiment_temp,
            "turnover_yi": turnover_today,
            "turnover_change_yi": round(turnover_today - turnover_prev, 1),
            "max_board_height": max_height[-1],
            "limit_up": limit_up_cnt[-1], "limit_down": limit_down_cnt[-1],
            "boards3": boards3[-1], "boards4": boards4[-1], "boards5plus": boards5[-1],
            "adv_dec_ratio": adv_dec_ratio,
            "advancers": adv_list[-1], "decliners": dec_list[-1],
            "above_ma5_pct": above_all[-1],
        },
        "turnover_trend": {"dates": dates, "amount_yi": amount_yi},
        "above_ma5_trend": {"dates": dates, "all": above_all, **above_cap},
        "limit_distribution": {"dates": dates, "up": limit_up_cnt, "down": limit_down_cnt},
        "board_ladder": {"dates": dates, "b3": boards3, "b4": boards4, "b5plus": boards5, "max_height": max_height},
        "cause_ladder": cause_ladder[:12],
        "index_returns": {"dates": dates, **seg_returns},
    }


def _sentiment_temperature(above_ma5_pct: float, adv_dec_ratio: float, up: int, down: int) -> int:
    # 三因子合成 0-100：5日线占比(权重0.45) + 涨跌比(0.30) + 涨跌停强弱(0.25)
    ratio_score = min(100.0, adv_dec_ratio / 3.0 * 100) if adv_dec_ratio else 0.0
    limit_total = up + down
    limit_score = (up / limit_total * 100) if limit_total else 50.0
    temp = above_ma5_pct * 0.45 + ratio_score * 0.30 + limit_score * 0.25
    return int(max(0, min(100, round(temp))))
