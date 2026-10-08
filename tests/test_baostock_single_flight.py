"""baostock 不是线程安全的：一条没结束（包括超时被弃的线程）时，下一次调用必须立刻让位，
不能再开新线程排队（2026-10-08 同步时堆了 112 条线程卡在 login）。"""
import threading
import time

import pytest

from quantcore.quant import data_sources


def test_second_call_fails_fast_while_first_hangs(monkeypatch):
    release = threading.Event()
    src = data_sources.BaoStockSource()
    monkeypatch.setattr(src, "_login", lambda: release.wait(5) or (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(data_sources, "_call_with_timeout", lambda f, t, l: threading.Thread(target=f, daemon=True).start())
    src.history("600000", "2026-01-01", None)          # 第一条挂住
    t0 = time.perf_counter()
    with pytest.raises(RuntimeError, match="busy"):
        src.history("600001", "2026-01-01", None)
    assert time.perf_counter() - t0 < 0.5
    release.set()
    for _ in range(50):                                 # 第一条结束后闸门放开
        if not data_sources._BAOSTOCK_BUSY.locked():
            break
        time.sleep(0.05)
    assert not data_sources._BAOSTOCK_BUSY.locked()
