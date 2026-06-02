from __future__ import annotations

from typing import Callable, Dict, List, Optional

import pandas as pd

from .factors import enrich_indicators
from .models import BacktestResult


def run_long_only_backtest(
    symbol: str,
    df: pd.DataFrame,
    signal_func: Callable[[pd.DataFrame], pd.Series],
    strategy_name: str,
    initial_cash: float = 100000.0,
    stop_loss_pct: float = 0.0,
) -> BacktestResult:
    data = enrich_indicators(df)
    raw_signal = signal_func(data).fillna(False)
    if stop_loss_pct and stop_loss_pct > 0:
        # Stateful: hold while signal true, force-exit if close falls below
        # entry * (1 - stop_loss_pct). Trades execute next bar (position.shift(1)).
        closes = data["close"].tolist()
        sig = raw_signal.astype(bool).tolist()
        held: List[int] = []
        in_pos = False
        entry = 0.0
        for i in range(len(data)):
            if in_pos:
                if closes[i] <= entry * (1 - stop_loss_pct) or not sig[i]:
                    in_pos = False
                    entry = 0.0
            if not in_pos and sig[i]:
                in_pos = True
                entry = closes[i]
            held.append(1 if in_pos else 0)
        position = pd.Series(held, index=data.index).shift(1).fillna(0)
    else:
        position = raw_signal.astype(int).shift(1).fillna(0)
    returns = data["close"].pct_change().fillna(0)
    strategy_returns = returns * position
    equity = initial_cash * (1 + strategy_returns).cumprod()

    previous_position = position.shift(1).fillna(0)
    entries = (position == 1) & (previous_position == 0)
    exits = (position == 0) & (previous_position == 1)
    trades = int(entries.sum())

    trade_returns: List[float] = []
    entry_price: Optional[float] = None
    for idx, row in data.iterrows():
        if bool(entries.iloc[idx]):
            entry_price = float(row["close"])
        if bool(exits.iloc[idx]) and entry_price:
            trade_returns.append(float(row["close"]) / entry_price - 1)
            entry_price = None

    if entry_price:
        trade_returns.append(float(data["close"].iloc[-1]) / entry_price - 1)

    win_rate = sum(1 for ret in trade_returns if ret > 0) / len(trade_returns) if trade_returns else 0.0
    running_peak = equity.cummax()
    max_drawdown = float((equity / running_peak - 1).min()) if not equity.empty else 0.0
    total_return = float(equity.iloc[-1] / initial_cash - 1) if not equity.empty else 0.0
    days = max((data["date"].iloc[-1] - data["date"].iloc[0]).days, 1) if "date" in data else len(data)
    annualized = (1 + total_return) ** (365 / days) - 1 if total_return > -1 else -1.0

    calmar_ratio = round(float(annualized) / abs(max_drawdown), 4) if max_drawdown != 0 else 0.0

    winning = [r for r in trade_returns if r > 0]
    losing = [abs(r) for r in trade_returns if r < 0]
    avg_win = sum(winning) / len(winning) if winning else 0.0
    avg_loss = sum(losing) / len(losing) if losing else 0.0
    profit_loss_ratio = round(avg_win / avg_loss, 4) if avg_loss > 0 else 0.0

    curve: List[Dict] = []
    for _, row in pd.DataFrame({"date": data["date"], "equity": equity, "position": position}).tail(240).iterrows():
        curve.append({
            "date": row["date"].strftime("%Y-%m-%d") if hasattr(row["date"], "strftime") else str(row["date"]),
            "equity": round(float(row["equity"]), 2),
            "position": int(row["position"]),
        })

    return BacktestResult(
        symbol=symbol,
        strategy=strategy_name,
        start_date=data["date"].iloc[0].strftime("%Y-%m-%d") if "date" in data and not data.empty else None,
        end_date=data["date"].iloc[-1].strftime("%Y-%m-%d") if "date" in data and not data.empty else None,
        initial_cash=initial_cash,
        final_value=round(float(equity.iloc[-1]), 2) if not equity.empty else initial_cash,
        total_return=round(total_return, 4),
        annualized_return=round(float(annualized), 4),
        max_drawdown=round(max_drawdown, 4),
        win_rate=round(float(win_rate), 4),
        calmar_ratio=calmar_ratio,
        profit_loss_ratio=profit_loss_ratio,
        trades=trades,
        equity_curve=curve,
    )
