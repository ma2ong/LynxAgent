"""ML 因子计算在子进程里跑（不在 Web 进程抢 GIL）：能跨进程完成并把结果带回来。"""
from quantcore.quant.ml import service


def test_compute_round_trips_through_a_child_process():
    """函数与参数能 pickle 进子进程、结果能带回（空测试库返回 {"error": "no data"}）。"""
    payload = service._compute_isolated(5, 5, 10, "once", False, 20, 250)
    assert isinstance(payload, dict)


def test_run_ml_factor_uses_isolation(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "_compute_isolated", lambda *a: calls.append(a) or {"error": "no data"})
    monkeypatch.setattr(service, "_compute", lambda *a: (_ for _ in ()).throw(AssertionError("在 Web 进程里算了")))
    service.run_ml_factor(universe_limit=5, force=True)
    assert calls
