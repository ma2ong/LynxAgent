import polars as pl
import numpy as np


class ValuationCalculator:

    def percentile(self, current: float, history: list[float]) -> float:
        if not history:
            raise ValueError("history must not be empty")
        arr = np.array(history)
        return float(np.mean(arr <= current))

    def compute_valuation_bands(self, df: pl.DataFrame, metric: str) -> dict:
        series = df[metric].drop_nulls().to_list()
        if not series:
            return {}
        current = series[-1]
        arr = np.array(series)
        mean = float(arr.mean())
        std = float(arr.std())
        return {
            "metric": metric,
            "current": round(current, 2),
            "percentile": round(self.percentile(current, series), 3),
            "mean": round(mean, 2),
            "std": round(std, 2),
            "low_1std": round(mean - std, 2),
            "high_1std": round(mean + std, 2),
            "low_2std": round(mean - 2 * std, 2),
            "high_2std": round(mean + 2 * std, 2),
        }
