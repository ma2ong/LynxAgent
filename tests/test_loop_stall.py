"""事件循环卡顿记录器：循环被同步阻塞时要记下阻塞点的调用栈。"""
import asyncio
import time

from app.core import loop_stall


def test_blocked_loop_is_logged_with_culprit(tmp_path, monkeypatch):
    log = tmp_path / "stall.log"
    monkeypatch.setattr(loop_stall, "LOG_PATH", log)
    monkeypatch.setattr(loop_stall, "STALL_SECONDS", 1.0)
    monkeypatch.setattr(loop_stall, "_started", False)

    def culprit_blocking_call():
        time.sleep(2.2)

    async def main():
        loop_stall.start(asyncio.get_running_loop())
        await asyncio.sleep(0.6)
        culprit_blocking_call()
        await asyncio.sleep(0.6)

    asyncio.run(main())
    text = log.read_text(encoding="utf-8")
    assert "事件循环线程" in text
    assert "culprit_blocking_call" in text
