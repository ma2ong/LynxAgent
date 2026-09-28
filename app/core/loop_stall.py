"""事件循环卡顿记录器：主循环卡住超过阈值时，把卡住那一刻的调用栈写进日志。

为什么要它：watchdog 每天都记到「port open but health failed」（/api/health 只返回一个
常量，它超时只可能是事件循环被堵死或 GIL 被抢光），但从来不知道当时在跑什么，
嫌疑名单只能靠猜（见记忆 lynxagent-backend-health-chronic）。

做法：守护线程每 0.5s 往循环里投一个回调打时间戳；时间戳超过 STALL_SECONDS 没更新，
就把**所有线程**的栈抓下来（循环线程的栈说明谁在堵循环；工作线程的栈说明谁在抢 GIL），
每次卡顿只记一次。开销是每 0.5s 一个空回调，可忽略。
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

STALL_SECONDS = float(os.getenv("LYNX_LOOP_STALL_SECONDS", "3"))
LOG_PATH = Path(__file__).resolve().parents[2] / "runtime" / "loop_stall.log"
MAX_LOG_BYTES = 5 * 1024 * 1024

_started = False


def _dump(loop_thread_id: int, stalled_for: float) -> None:
    frames = sys._current_frames()
    names = {t.ident: t.name for t in threading.enumerate()}
    lines = [f"\n===== {datetime.now().isoformat(timespec='seconds')} 事件循环已卡 {stalled_for:.1f}s ====="]
    order = [loop_thread_id] + [tid for tid in frames if tid != loop_thread_id]
    for tid in order:
        frame = frames.get(tid)
        if frame is None:
            continue
        stack = traceback.extract_stack(frame)
        # 空闲的线程池工人都停在 queue.get / select，没有信息量
        if tid != loop_thread_id and stack and stack[-1].name in {"wait", "get", "select", "_worker", "sleep"}:
            continue
        tag = "【事件循环线程】" if tid == loop_thread_id else ""
        lines.append(f"--- 线程 {names.get(tid, tid)} {tag}")
        lines.extend(line.rstrip() for line in traceback.format_list(stack[-12:]))
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > MAX_LOG_BYTES:
            LOG_PATH.replace(LOG_PATH.with_suffix(".log.1"))
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass


def start(loop: asyncio.AbstractEventLoop) -> None:
    global _started
    if _started or STALL_SECONDS <= 0:
        return
    _started = True
    loop_thread_id = threading.get_ident()
    state = {"beat": time.monotonic()}

    def _beat() -> None:
        state["beat"] = time.monotonic()

    def _watch() -> None:
        reported = False
        while True:
            time.sleep(0.5)
            try:
                loop.call_soon_threadsafe(_beat)
            except RuntimeError:        # 循环已关闭
                return
            lag = time.monotonic() - state["beat"]
            if lag >= STALL_SECONDS and not reported:
                _dump(loop_thread_id, lag)
                reported = True
            elif lag < 1.0:
                reported = False

    threading.Thread(target=_watch, name="loop-stall-watch", daemon=True).start()
