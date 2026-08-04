"""把日线历史整段重拉到 2020（现有数据只到 2024-05）。

**为什么是整段重拉而不是补前面那一段**：现有数据来自腾讯 `fqkline`，新数据来自新浪。
两家都叫「前复权」，但除权因子不同，同一天同一只票的收盘价差 0.967~1.025 倍，**且每只
票的比值都不一样**（分红节奏不同）。拼接会在接缝日给全市场每只票凭空造出一段假涨跌，
污染所有跨越该日的回测。所以要么整段用腾讯，要么整段用新浪，不能混。

**为什么选新浪**（2026-08-03 实测，别再重复踩）：
- 腾讯 `fqkline` 把 count 写死 800 且反爬敏感，密集请求会 IP 级 501；
- `push2his.eastmoney.com`（akshare `stock_zh_a_hist`）直连被拒，走 Windows 系统代理时通时断；
- `baostock` 登录卡死；网易 `chddata` 502。
新浪 `stock_zh_a_daily` 一次请求就给整段（6 年半约 1600 根，1.5~3 秒），还附带真实成交额
（现有库的 amount 是 `close×手×100` 估算的）和流通股本、换手率。

**北交所不在范围内**：新浪对 bj920002 返回空，317 只北交所股票保持腾讯现状。各自整段
内部一致，所以回测不受污染，只是北交所可用历史短。副作用：北交所的 amount 仍是估算值，
与其余股票的真实成交额有约 5% 口径差，跨板块比成交额时留意。

**先落独立库**：全量要跑几十分钟到几小时，期间生产同步一直在写生产库。跑完校验通过
再 `--merge`，不直接动生产库。

    python scripts/backfill_history.py --limit 30 --workers 2   # 试速率
    python scripts/backfill_history.py --workers 4              # 全量，可中断续跑
    python scripts/backfill_history.py --verify                 # 校验
    python scripts/backfill_history.py --merge                  # 切换到生产库
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

PROD_DB = os.path.join(REPO, "runtime", "quant_data.sqlite")
WORK_DB = os.path.join(REPO, "runtime", "backfill.sqlite")
TARGET_START = "2020-01-01"
BJ_PREFIX = ("83", "87", "88", "92", "43")   # 北交所，新浪不覆盖

_write_lock = threading.Lock()


def sina_symbol(symbol: str) -> str:
    return ("sh" if symbol[0] in "56" else "sz") + symbol


def init_work_db() -> sqlite3.Connection:
    con = sqlite3.connect(WORK_DB, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS daily_kline("
                "symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL,"
                "volume REAL, amount REAL, PRIMARY KEY(symbol,date))")
    con.execute("CREATE TABLE IF NOT EXISTS done("
                "symbol TEXT PRIMARY KEY, bars INT, note TEXT, at TEXT)")
    con.commit()
    return con


def targets(work: sqlite3.Connection, limit: int | None) -> list[str]:
    prod = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
    syms = [r[0] for r in prod.execute("SELECT DISTINCT symbol FROM daily_kline ORDER BY symbol")]
    prod.close()
    done = {r[0] for r in work.execute("SELECT symbol FROM done")}
    out = [s for s in syms if s[:2] not in BJ_PREFIX and s not in done]
    return out[:limit] if limit else out


def fetch_one(symbol: str, end: str) -> tuple[str, list[tuple], str]:
    """拉一只票的整段历史。返回 (代码, 行, 备注)。"""
    import akshare as ak
    df = ak.stock_zh_a_daily(symbol=sina_symbol(symbol), start_date=TARGET_START.replace("-", ""),
                             end_date=end.replace("-", ""), adjust="qfq")
    if df is None or df.empty:
        return symbol, [], "empty"
    rows = []
    for r in df.itertuples(index=False):
        d = r.date.strftime("%Y-%m-%d") if hasattr(r.date, "strftime") else str(r.date)[:10]
        close = float(r.close)
        if not (close > 0):
            continue
        # 新浪的 volume 是「股」，生产库存「手」，实测比值精确为 100（含科创板，无 100 倍陷阱）。
        # amount 是真实成交额（元），优于现有库的 close×手×100 估算。
        rows.append((symbol, d, float(r.open), float(r.high), float(r.low), close,
                     float(r.volume) / 100.0, float(r.amount)))
    return symbol, rows, "ok"


def run(limit: int | None, workers: int, delay: float) -> None:
    work = init_work_db()
    todo = targets(work, limit)
    end = date.today().strftime("%Y-%m-%d")
    print(f"待重拉 {len(todo)} 只（{TARGET_START} → {end}，{workers} 线程）", flush=True)
    if not todo:
        return
    ok = empty = failed = 0
    t0 = time.time()

    def job(sym: str):
        if delay:
            time.sleep(delay)
        return fetch_one(sym, end)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(job, s): s for s in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            sym = futures[fut]
            try:
                sym, rows, note = fut.result()
            except Exception as exc:            # 网络/解析/限流都归到失败，留待续跑
                failed += 1
                note, rows = f"{type(exc).__name__}: {str(exc)[:60]}", []
                if failed <= 10 or failed % 50 == 0:
                    print(f"  ✗ {sym} {note}", flush=True)
            else:
                if rows:
                    ok += 1
                else:
                    empty += 1
            with _write_lock:
                if rows:
                    work.executemany("INSERT OR REPLACE INTO daily_kline VALUES(?,?,?,?,?,?,?,?)", rows)
                work.execute("INSERT OR REPLACE INTO done VALUES(?,?,?,?)",
                             (sym, len(rows), note, datetime.now().isoformat(timespec="seconds")))
                work.commit()
            if i % 100 == 0 or i == len(todo):
                rate = i / max(1e-9, time.time() - t0)
                print(f"  [{i}/{len(todo)}] 成功{ok} 空{empty} 失败{failed} | "
                      f"{rate:.1f} 只/秒 ETA {(len(todo)-i)/max(1e-9,rate)/60:.0f} 分", flush=True)
    print(f"完成：成功 {ok}，空 {empty}，失败 {failed}，耗时 {(time.time()-t0)/60:.1f} 分", flush=True)


def verify() -> None:
    """切换前的体检。重点是量纲：历史上科创板成交额被放大 100 倍就是这么漏过去的。"""
    if not os.path.exists(WORK_DB):
        print("还没有回补库")
        return
    w = sqlite3.connect(f"file:{WORK_DB}?mode=ro", uri=True)
    n, ns, d0, d1 = w.execute(
        "SELECT COUNT(*),COUNT(DISTINCT symbol),MIN(date),MAX(date) FROM daily_kline").fetchone()
    print(f"回补库：{n:,} 行 / {ns} 只 / {d0} → {d1}")
    fail = w.execute("SELECT COUNT(*) FROM done WHERE note!='ok'").fetchone()[0]
    print(f"未成功：{fail} 只（note != ok）")

    print("\n跨板块日均成交额中位（元）—— 各板块之间不应差出数量级：")
    for pre, label in [("6", "沪主板"), ("0", "深主板"), ("3", "创业板"), ("688", "科创板")]:
        row = w.execute(
            "SELECT COUNT(*), AVG(amount) FROM daily_kline "
            "WHERE symbol LIKE ?||'%' AND date>='2025-01-01'", (pre,)).fetchone()
        if row[0]:
            print(f"  {label:6s} {row[0]:>9,} 行  均额 {row[1]/1e8:.3f} 亿")

    print("\n与生产库重叠段对比（同源应完全一致的只有量，价会因复权基准不同而整体缩放）：")
    p = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
    for sym in ("600519", "000001", "300750", "688981"):
        a = w.execute("SELECT close,volume FROM daily_kline WHERE symbol=? AND date='2025-06-03'",
                      (sym,)).fetchone()
        b = p.execute("SELECT close,volume FROM daily_kline WHERE symbol=? AND date='2025-06-03'",
                      (sym,)).fetchone()
        if a and b and b[0] and b[1]:
            print(f"  {sym}: 价比 {a[0]/b[0]:.4f}  量比 {a[1]/b[1]:.4f}（量比应≈1.000）")

    print("\n每年交易日数（应在 240 上下，缺口会在这里露出来）：")
    for y, c in w.execute("SELECT substr(date,1,4), COUNT(DISTINCT date) FROM daily_kline "
                          "GROUP BY 1 ORDER BY 1"):
        print(f"  {y}: {c} 天")
    w.close()
    p.close()


def merge() -> None:
    """用回补库替换生产库里这些股票的日线。北交所（不在回补范围）原样保留。"""
    if not os.path.exists(WORK_DB):
        print("还没有回补库")
        return
    con = sqlite3.connect(PROD_DB)
    con.execute("ATTACH DATABASE ? AS bf", (WORK_DB,))
    before = con.execute("SELECT COUNT(*) FROM daily_kline").fetchone()[0]
    # 整段替换：先删掉回补库覆盖到的股票的所有旧行，再灌入，避免新旧复权基准混在同一只票里
    con.execute("DELETE FROM daily_kline WHERE symbol IN (SELECT DISTINCT symbol FROM bf.daily_kline)")
    con.execute("INSERT INTO daily_kline(symbol,date,open,high,low,close,volume,amount) "
                "SELECT symbol,date,open,high,low,close,volume,amount FROM bf.daily_kline")
    con.commit()
    after, d0, d1 = con.execute("SELECT COUNT(*),MIN(date),MAX(date) FROM daily_kline").fetchone()
    con.execute("DETACH DATABASE bf")
    con.close()
    print(f"生产库 {before:,} → {after:,} 行，范围 {d0} → {d1}")
    print("提醒：已存的 replay_results 是按旧价格算的，需要重跑或标记作废。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--delay", type=float, default=0.0, help="每个请求前的等待秒数")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--merge", action="store_true")
    a = ap.parse_args()
    if a.verify:
        verify()
    elif a.merge:
        merge()
    else:
        run(a.limit, a.workers, a.delay)
