"""慢速重拉前复权历史，修复「除权后本地历史未重算」的存量数据（2026-09-29）。

背景见记忆 lynxagent-intraday-bar-and-adjust：回补存的是回补当时的前复权价，之后的分红送转
从未重算，除权日出现假跌。每日同步已能自动识别新的除权，这个脚本只修存量。

吸取当天两次全量重建触发腾讯 WAF 的教训：
- 单线程 + 固定间隔（默认 0.5 秒/只 ≈ 每秒 2 个请求），不并发猛打；
- 连续 N 只拉空即停（被限流时继续打只会延长封禁）；
- 收盘前（15:05 前）丢弃当天未收盘的 bar；
- 进度写 runtime/repair_adjusted_history.progress，中断后用 --resume 从断点继续。

用法：
    python scripts/repair_adjusted_history.py --limit 50        # 先试 50 只
    python scripts/repair_adjusted_history.py --resume          # 全量（断点续跑）
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PROGRESS = ROOT / "runtime" / "repair_adjusted_history.progress"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--breaker", type=int, default=10)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    from quantcore.quant.local_store import get_local_store
    from quantcore.quant.sync_service import FULL_HISTORY_DAYS, MarketSyncService

    store = get_local_store()
    svc = MarketSyncService(store=store, max_workers=1)
    syms = [r[0] for r in store._conn().execute("SELECT symbol FROM stock_meta ORDER BY symbol")]
    done = set()
    if args.resume and PROGRESS.exists():
        done = set(PROGRESS.read_text(encoding="utf-8").split())
    todo = [s for s in syms if s not in done]
    if args.limit:
        todo = todo[: args.limit]
    start = (date.today() - timedelta(days=FULL_HISTORY_DAYS)).strftime("%Y-%m-%d")
    today = date.today().strftime("%Y-%m-%d")
    print(f"待修 {len(todo)} 只（已完成 {len(done)}），间隔 {args.interval}s", flush=True)

    streak = written = ok = 0
    t0 = time.time()
    with PROGRESS.open("a", encoding="utf-8") as prog:
        for i, sym in enumerate(todo, 1):
            df = svc._fetch_kline(sym, start)
            if df is None or df.empty:
                streak += 1
                if streak >= args.breaker:
                    print(f"连续 {streak} 只拉空，疑似被限流，停止。已处理 {i}/{len(todo)}", flush=True)
                    break
            else:
                streak = 0
                if datetime.now().time() < dtime(15, 5):
                    df = df[df["date"].astype(str).str[:10] != today]
                written += store.upsert_kline(sym, df)
                ok += 1
                prog.write(sym + "\n")
                prog.flush()
            if i % 50 == 0:
                rate = i / (time.time() - t0)
                print(f"{i}/{len(todo)} 成功 {ok} 写入 {written} 行 · {rate:.1f} 只/秒", flush=True)
            time.sleep(args.interval)
    print(f"结束：成功 {ok}/{len(todo)}，写入 {written} 行，用时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
