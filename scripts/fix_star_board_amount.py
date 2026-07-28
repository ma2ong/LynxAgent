"""一次性纠偏：科创板（688）历史日线的成交量/成交额单位错配。

腾讯对科创板按**股**给成交量，其余板块按**手**，而 sync_service 原来一律当成手、
再用 `close × volume × 100` 算成交额，于是 688 的 volume 与 amount 都被放大了 100 倍。
ingest 侧已在 `_volume_to_lots` 修好，这个脚本把库里已有的历史数据一并纠正。

volume 与 amount 同时除以 100，这样处理完仍然满足全库统一的
`amount ≈ close × volume × 100`（volume 单位=手），下次谁再从 volume 推 amount 也不会错。

影响面（跑之前请知悉）：成交额是选股候选的流动性闸门（smart/pattern 池的
`amount >= 3e7`）、liquidity 因子、热力图与盘报的成交额统计。纠偏后科创板不再被
系统性高估，**历史评分与回放结论会随之变化**，experiments/ 下的结论需要重跑。

    python scripts/fix_star_board_amount.py            # dry-run，只报告不改
    python scripts/fix_star_board_amount.py --apply    # 真正执行（自动先备份）
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(REPO, "runtime", "quant_data.sqlite")
PREFIX = "688"
SCALE = 100.0


def _report(conn: sqlite3.Connection) -> dict:
    star = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT symbol) FROM daily_kline WHERE symbol LIKE ?",
        (PREFIX + "%",)).fetchone()
    day = conn.execute("SELECT MAX(date) FROM daily_kline").fetchone()[0]
    tot = conn.execute("SELECT SUM(amount) FROM daily_kline WHERE date=?", (day,)).fetchone()[0] or 0
    star_med = conn.execute(
        "SELECT AVG(amount) FROM daily_kline WHERE date=? AND symbol LIKE ?",
        (day, PREFIX + "%")).fetchone()[0] or 0
    other_med = conn.execute(
        "SELECT AVG(amount) FROM daily_kline WHERE date=? AND symbol NOT LIKE ?",
        (day, PREFIX + "%")).fetchone()[0] or 0
    return {"rows": star[0], "symbols": star[1], "date": day,
            "market_total_wan_yi": tot / 1e12,
            "star_avg_yi": star_med / 1e8, "other_avg_yi": other_med / 1e8}


def _print(tag: str, r: dict) -> None:
    print(f"[{tag}] {r['date']}  全市场成交额合计 {r['market_total_wan_yi']:.2f} 万亿"
          f" | 科创板均值 {r['star_avg_yi']:.1f} 亿 | 其余均值 {r['other_avg_yi']:.1f} 亿")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--apply", action="store_true", help="真正写库；不加则只做 dry-run")
    a = ap.parse_args()

    if not os.path.exists(a.db):
        print(f"库不存在：{a.db}")
        return 1

    conn = sqlite3.connect(a.db, timeout=120)
    before = _report(conn)
    _print("before", before)
    print(f"        待纠偏 {before['rows']:,} 行 / {before['symbols']} 只科创板个股"
          f"（volume 与 amount 同时 ÷{SCALE:.0f}）")

    if before["star_avg_yi"] < before["other_avg_yi"] * 5:
        print("科创板成交额已与其他板块同量级，看起来已经纠偏过；不重复执行。")
        return 0

    if not a.apply:
        print("\ndry-run：未改动任何数据。确认无误后加 --apply 执行。")
        return 0

    backup = f"{a.db}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    print(f"\n备份 → {backup}")
    conn.close()
    shutil.copy2(a.db, backup)

    conn = sqlite3.connect(a.db, timeout=120)
    started = time.time()
    conn.execute(
        "UPDATE daily_kline SET amount = amount / ?, volume = volume / ? WHERE symbol LIKE ?",
        (SCALE, SCALE, PREFIX + "%"))
    conn.commit()
    print(f"更新完成，用时 {time.time() - started:.0f}s")

    after = _report(conn)
    _print("after ", after)
    conn.close()
    print(f"\n备份保留在 {backup}（确认无误后可自行删除）")
    print("提醒：候选流动性闸门与 liquidity 因子的口径变了，experiments/ 的结论需重跑。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
