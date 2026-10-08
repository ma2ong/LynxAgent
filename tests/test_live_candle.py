"""个股深研 K 线：盘中当日日线收盘后才落库，图上必须补一根实时 K 线，否则整天停在上一交易日
（2026-10-08：欣旺达页头 22.78 +3.6%，图却停在 09-30 的 22.0）。"""
from app.lite_main import _append_live_candle


def _kline():
    closes = [10.0 + i * 0.1 for i in range(25)]
    return {"dates": [f"2026-09-{i + 1:02d}" for i in range(25)], "open": closes, "high": closes,
            "low": closes, "close": closes, "volume": [1] * 25, "amount": [1] * 25,
            "ma5": [None] * 25, "ma10": [None] * 25, "ma20": [None] * 25}


QUOTE = {"price": 13.0, "open": 12.5, "high": 13.2, "low": 12.4, "volume": 5e6, "amount": 6e7,
         "updated_at": "2026/10/08 11:28:39"}


def test_appends_today_and_recomputes_ma():
    k = _append_live_candle(_kline(), QUOTE)
    assert k["dates"][-1] == "2026-10-08"
    assert (k["open"][-1], k["high"][-1], k["low"][-1], k["close"][-1]) == (12.5, 13.2, 12.4, 13.0)
    assert k["ma5"][-1] == round((13.0 + 12.4 + 12.3 + 12.2 + 12.1) / 5, 2)
    assert len({len(v) for v in k.values()}) == 1


def test_skips_when_quote_is_not_newer():
    k = _append_live_candle(_kline(), {**QUOTE, "updated_at": "2026/09/25 15:00:00"})
    assert k["dates"][-1] == "2026-09-25" and len(k["dates"]) == 25
