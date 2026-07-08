"""聪明钱（活跃席位聚合 / 席位胜率 / 基金重仓 纯变换）回归测试。"""
import math

import pandas as pd

from quantcore.quant.smart_money import _agg_active_seats, _shape_seat_winrate, _shape_fund_hold


def test_agg_active_seats_groups_and_ranks():
    df = pd.DataFrame([
        {"营业部名称": "席位A", "上榜日": "2026-07-01", "买入总金额": 2e8, "卖出总金额": 1e8, "买入股票": "甲 乙"},
        {"营业部名称": "席位A", "上榜日": "2026-07-03", "买入总金额": 3e8, "卖出总金额": 0.0, "买入股票": "丙"},
        {"营业部名称": "席位B", "上榜日": "2026-07-02", "买入总金额": 1e8, "卖出总金额": 5e8, "买入股票": ""},
    ])
    rows = _agg_active_seats(df, top=10)
    assert rows[0]["seat"] == "席位A"          # 净买额降序
    assert rows[0]["count"] == 2
    assert rows[0]["net_yi"] == 4.0            # (2+3-1)e8 -> 亿
    assert rows[0]["last_date"] == "2026-07-03"
    assert "丙" in rows[0]["stocks"]
    assert rows[1]["net_yi"] == -4.0


def test_agg_active_seats_nan_amounts_do_not_break_json():
    # 东财偶有某席位金额为 NaN，必须归零，否则 JSON 序列化 500（回归 bug）
    df = pd.DataFrame([
        {"营业部名称": "席位C", "上榜日": "2026-07-05", "买入总金额": float("nan"),
         "卖出总金额": 1e8, "买入股票": "丁"},
    ])
    rows = _agg_active_seats(df, top=10)
    assert not math.isnan(rows[0]["buy_yi"])
    assert rows[0]["buy_yi"] == 0.0
    assert rows[0]["net_yi"] == -1.0


def test_shape_seat_winrate_picks_5d_metrics():
    df = pd.DataFrame([
        {"营业部名称": "游资甲", "上榜后5天-买入次数": 30, "上榜后5天-平均涨幅": 2.5, "上榜后5天-上涨概率": 60.0,
         "上榜后1天-买入次数": 33, "上榜后1天-平均涨幅": 1.0, "上榜后1天-上涨概率": 55.0},
        {"营业部名称": "散户乙", "上榜后5天-买入次数": 2, "上榜后5天-平均涨幅": 9.9, "上涨概率": 0},
    ])
    rows = _shape_seat_winrate(df, min_trades=5, top=10)
    assert len(rows) == 1                       # 样本<5 的过滤
    assert rows[0] == {"seat": "游资甲", "trades_5d": 30, "avg_chg_5d": 2.5, "win_rate_5d": 60.0,
                       "avg_chg_1d": 1.0, "win_rate_1d": 55.0}


def test_shape_fund_hold_top_by_mv():
    df = pd.DataFrame([
        {"股票代码": "300750", "股票简称": "宁德时代", "持有基金家数": 2338, "持股市值": 1.6e11,
         "持股变化": "减仓", "持股变动比例": -22.87},
        {"股票代码": "600519", "股票简称": "贵州茅台", "持有基金家数": 2000, "持股市值": 2.0e11,
         "持股变化": "增仓", "持股变动比例": 1.5},
    ])
    rows = _shape_fund_hold(df, top=10)
    assert rows[0]["symbol"] == "600519"        # 持股市值降序
    assert rows[0]["mv_yi"] == 2000.0
    assert rows[0]["funds"] == 2000
    assert rows[1]["change"] == "减仓" and rows[1]["change_pct"] == -22.87
