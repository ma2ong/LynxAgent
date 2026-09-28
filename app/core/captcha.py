"""注册用的图形算术验证码：自托管、无外部服务依赖。

挡的是「脚本批量注册」，不是定向攻击——配合同 IP 每小时 5 次的注册限流，足够让
刷号成本高过收益。题目是两位数加减，答案存在进程内存（单进程部署），5 分钟过期、
一题只能验一次，验错也作废（防穷举）。
"""
from __future__ import annotations

import random
import secrets
import time

TTL_SECONDS = 300
_MAX_PENDING = 5000
_pending: dict[str, tuple[int, float]] = {}


def _purge(now: float) -> None:
    expired = [k for k, (_, t) in _pending.items() if now - t > TTL_SECONDS]
    for k in expired:
        _pending.pop(k, None)
    while len(_pending) > _MAX_PENDING:          # 被刷接口时别把内存撑爆
        _pending.pop(next(iter(_pending)))


def _svg(text: str) -> str:
    rnd = random.Random()
    chars = []
    x = 14
    for ch in text:
        y = 30 + rnd.randint(-5, 5)
        rot = rnd.randint(-22, 22)
        chars.append(f'<text x="{x}" y="{y}" transform="rotate({rot} {x} {y})" '
                     f'font-size="{rnd.randint(22, 27)}" font-family="monospace" font-weight="700" '
                     f'fill="#{rnd.randint(0x20, 0x70):02x}{rnd.randint(0x20, 0x70):02x}{rnd.randint(0x50, 0x90):02x}">{ch}</text>')
        x += 19
    lines = "".join(
        f'<line x1="{rnd.randint(0, 150)}" y1="{rnd.randint(0, 44)}" x2="{rnd.randint(0, 150)}" '
        f'y2="{rnd.randint(0, 44)}" stroke="#9aa3b0" stroke-width="1"/>' for _ in range(5))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="150" height="44" viewBox="0 0 150 44">'
            f'<rect width="150" height="44" fill="#f4f5f7"/>{lines}{"".join(chars)}</svg>')


def issue() -> dict[str, str]:
    now = time.time()
    _purge(now)
    a, b = random.randint(10, 49), random.randint(1, 9)
    op = random.choice("+-")
    answer = a + b if op == "+" else a - b
    cid = secrets.token_urlsafe(16)
    _pending[cid] = (answer, now)
    return {"captcha_id": cid, "svg": _svg(f"{a}{op}{b}=?")}


def verify(captcha_id: str | None, answer: str | None) -> bool:
    item = _pending.pop(str(captcha_id or ""), None)     # 一题只验一次，对错都作废
    if not item or time.time() - item[1] > TTL_SECONDS:
        return False
    try:
        return int(str(answer).strip()) == item[0]
    except (TypeError, ValueError):
        return False
