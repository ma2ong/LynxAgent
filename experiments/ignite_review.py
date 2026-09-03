# -*- coding: utf-8 -*-
"""「地量点火」上线后的样本外复判。

为什么需要这个文件
------------------
这条规则 2026-09-03 进排序时，证据是**边缘**的：ig2_best 在 T+3 / T+1 开盘买入口径
下匹配对照增量 +0.16pp、7 个年份方向一致，但 CI 下沿 -0.05、去右尾后 -0.02，
七关里倒在「增量显著」和「去右尾」两关，而且倒的那一关正是多重检验 —— 同批测了 11 条
变体，挑出最好的那条来看，p=0.0438 过不了 Holm 阈值。

对「挑出来的那条」只有一种正当的复核方式：**换一批它没见过的数据再看一次**。
线上留痕就是那批数据。所以上线时把过闸的票留痕名字写成「地量点火·过闸」，
就是为了今天能把它们单独捞出来算。

它不是重跑一次历史回测
----------------------
历史回测再跑一遍还是同一批数据、同一次挑选，结论不会独立。这里只吃 2026-09-03
之后真实发出去的名单，口径与当初审计完全一致（T+3、T+1 开盘买入、匹配对照），
所以数字可以直接和 +0.16pp 对照着读。

用法
----
    python experiments/ignite_review.py                  # 默认读生产库
    python experiments/ignite_review.py --horizon 5      # 顺带看看 T+5 是否仍失效
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime

import pandas as pd

# 计划任务里 stdout 被重定向到文件，Windows 默认还是 GBK，中文报告会整篇乱码/报错。
# 这里显式转 UTF-8；reconfigure 在 3.7+ 都有，失败也不该让复判本身挂掉。
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import rule_audit as ra  # noqa: E402

LIVE_DB = os.path.join(ROOT, "runtime", "quant_data.sqlite")
GO_LIVE = "2026-09-03"          # 规则进排序那天，早于此的留痕不算数
PATTERN_TAG = "地量点火·过闸"    # 只有过闸（板块也满足）的票才真的拿到了加分
BASELINE_INC = 0.16             # 当初审计的匹配对照增量，用来对照读
# 判定阈值。先定后算 —— 看到数字再定线，等于没有线。
MIN_SAMPLES_TO_JUDGE = 60       # 低于此只报进度，不下结论
MIN_CLUSTERS_TO_JUDGE = 20


def gated_picks(db: str, since: str) -> set[tuple[str, str]]:
    """捞出真正拿到加分的那些票。

    留痕的 patterns 字段是形态名的逗号拼接，过闸的写成「地量点火·过闸」，
    只标注没加分的写成「地量点火」或「地量点火 D+n」—— 前缀匹配会把后两者也捞进来，
    所以这里必须用带后缀的全名判断。
    """
    conn = sqlite3.connect(db, timeout=60)
    try:
        rows = conn.execute(
            "SELECT pick_date, symbol, patterns FROM picks_history "
            "WHERE pool='smart' AND pick_date >= ? AND patterns LIKE ?",
            (since, f"%{PATTERN_TAG}%"),
        ).fetchall()
    finally:
        conn.close()
    return {(str(d), str(s).zfill(6)) for d, s, _p in rows}


_LINES: list[str] = []


def say(line: str = "") -> None:
    """同时打到 stdout 和报告缓冲。定时任务无人看屏幕，报告文件才是产物。"""
    print(line)
    _LINES.append(line)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=LIVE_DB, help="生产库路径（默认 runtime/quant_data.sqlite）")
    ap.add_argument("--since", default=GO_LIVE, help="起算日，默认规则上线日")
    ap.add_argument("--horizon", type=int, default=3, help="持有天数，默认 3（规则的有效窗口）")
    args = ap.parse_args()

    picks = gated_picks(args.db, args.since)
    say(f"【地量点火 · 样本外复判】{datetime.now():%Y-%m-%d %H:%M}")
    say(f"口径：T+{args.horizon}、T+1 开盘买入、匹配对照；起算 {args.since}")
    say(f"当初审计（历史样本）：匹配对照增量 {BASELINE_INC:+.2f}pp，"
          f"CI 下沿 -0.05、去右尾 -0.02 —— 边缘，故有此复判\n")

    if not picks:
        say("线上还没有任何过闸的点火票。")
        say("这本身是个信息：四个形态条件加板块闸之后，命中率可能比预期低得多。")
        say("如果两个月后仍是零，问题不是规则好坏，是它几乎从不触发 —— 那就该放宽或撤掉。")
        return

    days = sorted({d for d, _ in picks})
    say(f"过闸留痕：{len(picks)} 笔，分布在 {len(days)} 个交易日"
          f"（{days[0]} ~ {days[-1]}）")

    panel = ra.build_panel(args.db, args.since, args.horizon, entry="open")
    mask = pd.Series(
        [(d, s) in picks for d, s in zip(panel["date"], panel["symbol"])],
        index=panel.index,
    )
    evaluable = int(mask.sum())
    say(f"其中 {evaluable} 笔已经走满 T+{args.horizon}，可以评估"
          f"（差额是最近几天还没到期的）\n")
    if evaluable == 0:
        say("还没有一笔走满持有期，下次再看。")
        return

    r = ra.audit_one(panel, mask, "ignite_live", args.horizon)
    inc = r.get("inc_excess")
    say(f"  平均超额        {r['avg_excess']:+.2f}pp   （中位 {r['median_excess']:+.2f}）")
    say(f"  胜率            {r['win_rate'] * 100:.1f}%")
    if inc is not None:
        say(f"  匹配对照增量    {inc:+.2f}pp   CI [{r['inc_ci_lo']:+.2f}, {r['inc_ci_hi']:+.2f}]")
    if r.get("inc_ex_tail") is not None:
        say(f"  去右尾后增量    {r['inc_ex_tail']:+.2f}pp")
    say(f"  样本 {r['samples']} 笔 / {r.get('clusters', 0)} 个交易日\n")

    # 判定。样本不够时只报进度 —— 二十几笔样本上的正负号没有意义，
    # 而「看着还行就留着」正是当初那 11 条变体挑一条的老毛病。
    if evaluable < MIN_SAMPLES_TO_JUDGE or r.get("clusters", 0) < MIN_CLUSTERS_TO_JUDGE:
        need_d = max(0, MIN_CLUSTERS_TO_JUDGE - r.get("clusters", 0))
        say(f"结论：样本不足以判定（需 ≥{MIN_SAMPLES_TO_JUDGE} 笔且 ≥{MIN_CLUSTERS_TO_JUDGE} "
              f"个交易日，还差约 {need_d} 个交易日）。")
        say("      先不动，继续攒。这几个数字现在只能当进度看，不能当结论。")
        return

    if inc is not None and inc > 0 and r.get("inc_ci_lo", -1) > 0:
        say("结论：线上样本支持当初的 +0.16pp，且置信区间下沿为正 —— 保留，可考虑提高加分。")
    elif inc is not None and inc > 0:
        say("结论：方向为正但仍与 0 分不开。保留现状、继续攒，不要在这个基础上加码。")
    else:
        say("结论：线上样本不支持这条规则。")
        say("      动作：设 LYNX_IGNITE_BONUS=0 关掉加分（形态照常显示），"
              "然后重启后端。")
        say("      这是上线时就写好的回退路径，不需要改代码。")


def _write_report() -> None:
    out_dir = os.path.join(ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"ignite-review-{datetime.now():%Y-%m-%d}.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(_LINES) + "\n")
    print(f"\n报告已写入 {path}")


if __name__ == "__main__":
    try:
        main()
    finally:
        _write_report()
