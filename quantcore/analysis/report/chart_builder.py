import polars as pl


class ChartBuilder:

    def build_kline(self, df: pl.DataFrame) -> dict:
        required = {"date", "open", "close", "low", "high"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"DataFrame missing required columns: {missing}")
        dates = df["date"].to_list()
        kline_data = [
            [row["open"], row["close"], row["low"], row["high"]]
            for row in df.select(["open", "close", "low", "high"]).to_dicts()
        ]
        volumes = df["volume"].to_list() if "volume" in df.columns else []
        closes = df["close"].to_list()
        ma5 = self._ma(closes, 5)
        ma20 = self._ma(closes, 20)
        ma60 = self._ma(closes, 60)
        return {
            "dates": dates,
            "kline_data": kline_data,
            "volumes": volumes,
            "ma5": ma5,
            "ma20": ma20,
            "ma60": ma60,
        }

    def build_radar(self, scores: dict) -> dict:
        dim_labels = {
            "fundamental": "基本面", "governance": "公司治理",
            "competitive": "竞争力", "growth": "成长性", "valuation": "估值",
        }
        indicators = [{"name": dim_labels.get(k, k), "max": 100} for k in scores]
        values = list(scores.values())
        return {"indicators": indicators, "values": values}

    def build_valuation_history(self, dates: list, values: list, metric_name: str) -> dict:
        return {"dates": dates, "values": values, "name": metric_name}

    def _ma(self, prices: list[float], window: int) -> list[float | None]:
        result = []
        for i in range(len(prices)):
            if i < window - 1:
                result.append(None)
            else:
                result.append(round(sum(prices[i - window + 1:i + 1]) / window, 3))
        return result
