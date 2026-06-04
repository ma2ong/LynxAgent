"""A 股基本面（akshare）。供个股研报 A 的财务摘要 / 关键指标，与流水线 B 的 critic 共用。

akshare 各版本字段名不稳定，这里全部按关键词模糊匹配，缺字段返回 None，整体失败返回空 dict。
"""
from __future__ import annotations

from typing import Dict, Optional


def _num(value) -> Optional[float]:
    if value is None:
        return None
    s = str(value).strip().replace(",", "").replace("%", "")
    if s in ("", "-", "--", "nan", "None"):
        return None
    try:
        return float(s)
    except Exception:
        return None


def profile(symbol: str) -> Dict[str, object]:
    """个股概况：名称/行业/总市值/PE/PB（来自东财个股信息）。"""
    symbol = str(symbol).zfill(6)
    try:
        import akshare as ak

        df = ak.stock_individual_info_em(symbol=symbol)
    except Exception:
        return {}
    if df is None or df.empty:
        return {}
    kv = dict(zip(df.iloc[:, 0].astype(str), df.iloc[:, 1]))

    def pick(*keys):
        for k in keys:
            for col, val in kv.items():
                if k in col:
                    return val
        return None

    return {
        "name": str(pick("股票简称", "简称") or "").strip(),
        "industry": str(pick("行业") or "").strip(),
        "total_mv": _num(pick("总市值")),
        "float_mv": _num(pick("流通市值")),
        "listing_date": str(pick("上市时间") or "").strip(),
    }


def fundamentals(symbol: str) -> Dict[str, Optional[float]]:
    """核心财务摘要：营收/毛利率/净利率/经营现金流/ROE 等 + 同比。

    返回字段（缺则 None）：
      revenue, revenue_yoy, gross_margin, net_margin, net_profit, net_profit_yoy,
      operating_cashflow, roe, eps
    """
    symbol = str(symbol).zfill(6)
    try:
        import akshare as ak

        df = ak.stock_financial_abstract(symbol=symbol)
    except Exception:
        return {}
    if df is None or df.empty:
        return {}

    # stock_financial_abstract 形如: 第0列='选项'(类别), 第1列='指标'(指标名), 其余为报告期。
    # 必须精确选 '指标' 列；若按关键词搜索会先命中 '选项' 导致所有 metric() 匹配失败。
    idx_col = next(
        (c for c in df.columns if str(c) == "指标"),
        df.columns[1] if len(df.columns) > 1 else df.columns[0],
    )
    period_cols = [c for c in df.columns if c not in (df.columns[0], idx_col) and str(c)[0:1].isdigit()]
    # 取最近两期用于同比
    if not period_cols:
        return {}
    latest = period_cols[0]
    prev_year = period_cols[4] if len(period_cols) > 4 else (period_cols[-1] if len(period_cols) > 1 else None)

    rows = {str(name): row for name, row in zip(df[idx_col].astype(str), df.itertuples(index=False))}

    def metric(*keys, col=latest):
        for name, row in zip(df[idx_col].astype(str), range(len(df))):
            if any(k in name for k in keys):
                return _num(df.iloc[row][col])
        return None

    def yoy(*keys):
        cur = metric(*keys, col=latest)
        if prev_year is None or cur is None:
            return None
        base = metric(*keys, col=prev_year)
        if base in (None, 0):
            return None
        return round((cur - base) / abs(base) * 100, 2)

    return {
        "revenue": metric("营业总收入", "营业收入"),
        "revenue_yoy": yoy("营业总收入", "营业收入"),
        "net_profit": metric("归母净利润", "净利润"),
        "net_profit_yoy": yoy("归母净利润", "净利润"),
        "gross_margin": metric("毛利率", "销售毛利率"),
        "net_margin": metric("净利率", "销售净利率"),
        "roe": metric("净资产收益率", "ROE"),
        "operating_cashflow": metric("经营活动产生的现金流量净额", "经营现金流"),
        "eps": metric("每股收益", "基本每股收益"),
    }
