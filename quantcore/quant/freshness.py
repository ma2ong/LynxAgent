"""新鲜度标记：这个数算于什么时候、用的是哪天的数据、还能不能信。

为什么要有
----------
页面上的每个数字背后都可能是一份缓存：板块轮动和市场宽度按交易日缓存半天，热力图
60 秒，智选池每天预热一次。用户（和写代码的人）看到的只是数字本身，看不出它是刚算的
还是昨天的 —— 2026-08-31 就发生过拿 8-25 的快照跑当天分析、结论差点被当成最新的事。

两个轴必须分开报，它们会各自变旧：
  · 数据截至哪天（as_of）—— 日线同步没跟上时，重算一百遍还是旧数据；
  · 这份结果算于什么时候（computed_at）—— 数据是新的，但缓存是几小时前的。
只报其中一个都会给出误导性的"新鲜"。

档位
----
fresh  两个轴都新：数据是最新交易日，且刚算过。
aging  还能用，但已经旧到值得说一句。
stale  别拿它下判断了。数据落后于最新交易日时直接进这一档，无论算得多勤。
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

# 计算时效的两道线（秒）。半天那条对齐按交易日缓存的那批端点：同一天内重算与否
# 不改变结果，所以四小时内都算"还行"，超过就提示可以刷新了。
FRESH_SECONDS = 15 * 60
AGING_SECONDS = 4 * 3600

LABELS = {"fresh": "最新", "aging": "略旧", "stale": "已过期"}


def mark(as_of: str, computed_at: Optional[float] = None,
         latest_bar: Optional[str] = None) -> Dict[str, Any]:
    """给一份结果打新鲜度标记。

    as_of        这份结果用的数据截至哪个交易日
    computed_at  算出来的时刻（time.time()）；缺省当作此刻
    latest_bar   库里最新的真实交易日；给了才能判断数据本身是否落后
    """
    now = time.time()
    computed_at = float(computed_at if computed_at is not None else now)
    age = max(0.0, now - computed_at)

    # 数据落后于最新交易日是硬伤：这时候"刚算过"毫无意义，重算出来还是旧的
    data_behind = bool(latest_bar and as_of and str(as_of) < str(latest_bar))
    if data_behind:
        state = "stale"
    elif age <= FRESH_SECONDS:
        state = "fresh"
    elif age <= AGING_SECONDS:
        state = "aging"
    else:
        state = "stale"

    return {
        "as_of": as_of or None,
        "latest_bar": latest_bar or None,
        "computed_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(computed_at)),
        "age_seconds": int(age),
        "data_behind": data_behind,
        "state": state,
        "label": LABELS[state],
    }
