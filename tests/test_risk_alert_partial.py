"""风险扫描缓存冷（刚重启）时，仪表缺破位广度/龙头崩塌两项、分数偏低约 20 分。
必须标明 partial，且不能把这份偏乐观的结果缓存满 30 秒（2026-10-08：重启后把「危险」显示成「安全」）。"""
import asyncio

from app.routers import quant


def test_cold_scan_marks_partial_and_short_cache(monkeypatch):
    async def snap():
        return {}

    calls = []

    def fake_ctx(_snapshot):
        calls.append(1)
        return {"daily": [], "temp": 50.0}

    monkeypatch.setattr(quant, "_load_snapshot", snap)
    monkeypatch.setattr(quant, "_risk_scan_cached", lambda *_a, **_k: {})
    monkeypatch.setattr("quantcore.quant.engine.market_context", fake_ctx)
    monkeypatch.setattr("quantcore.quant.macro_bar.fetch_index_quotes", lambda: [])
    monkeypatch.setattr(quant, "_cold_excess", lambda: None)
    quant._RISK_ALERT_CACHE.clear()

    first = asyncio.run(quant.quant_risk_alert())
    assert first["partial"] == ["破位广度", "龙头崩塌"]
    ts, _ = quant._RISK_ALERT_CACHE["v"]
    import time
    assert time.time() - ts >= 25        # 30 秒缓存只剩约 5 秒
