from __future__ import annotations

from typing import Callable, List

import pandas as pd

from .backtest import run_long_only_backtest
from .models import BacktestResult


class BacktraderUnavailable(RuntimeError):
    pass


def is_backtrader_available() -> bool:
    try:
        import backtrader  # noqa: F401
        return True
    except Exception:
        return False


def run_backtrader_backtest(
    symbol: str,
    df: pd.DataFrame,
    signal_func: Callable[[pd.DataFrame], pd.Series],
    strategy_name: str,
    initial_cash: float = 100000.0,
) -> BacktestResult:
    """Run the same signal through Backtrader when the package is installed.

    The adapter keeps signal generation inside our quant layer and delegates
    order accounting to Backtrader. This avoids duplicating strategy classes for
    every factor signal.
    """
    try:
        import backtrader as bt
    except Exception as exc:
        raise BacktraderUnavailable("backtrader is not installed") from exc

    data = df.copy()
    if data.empty:
        raise ValueError(f"{symbol} has no backtest data")
    data["signal"] = signal_func(data).fillna(False).astype(int)
    data = data.set_index(pd.to_datetime(data["date"]))

    class SignalData(bt.feeds.PandasData):
        lines = ("signal",)
        params = (("signal", -1),)

    class SignalStrategy(bt.Strategy):
        def next(self):
            signal = bool(self.datas[0].signal[0])
            if signal and not self.position:
                size = int(self.broker.getcash() / self.datas[0].close[0])
                if size > 0:
                    self.buy(size=size)
            elif not signal and self.position:
                self.close()

    cerebro = bt.Cerebro()
    cerebro.broker.setcash(initial_cash)
    cerebro.adddata(SignalData(dataname=data))
    cerebro.addstrategy(SignalStrategy)
    cerebro.run()

    # Keep the response contract identical to the vector backtest. Backtrader is
    # used for execution feasibility; metrics are still calculated centrally.
    result = run_long_only_backtest(symbol, df, signal_func, strategy_name, initial_cash)
    result.engine = "backtrader"
    result.final_value = round(float(cerebro.broker.getvalue()), 2)
    if result.equity_curve:
        result.equity_curve[-1]["equity"] = result.final_value
    return result
