"""把生产量化库拷成一份实验用快照。

实验必须在快照上跑，有两个原因：

1. 生产后端一直在写这个库（板块保温、数据同步、留痕）。SQLite 是 WAL，读不阻塞写，
   但写与写互斥；实验一旦要写自己的中间结果就会跟生产抢锁，轻则等，重则直接
   "database is locked"。
2. 更要紧的是别污染生产账本。曾经在生产库上跑探针，它的僵尸行清理顺手把生产
   正在跑的那条回放标成了 failed。

`sqlite3.Connection.backup()` 是在线一致性拷贝，不需要停生产，1GB 实测约 7 秒。

用法：
    python experiments/snapshot_db.py                 # -> experiments/.snapshot.sqlite
    python experiments/snapshot_db.py --out other.sqlite
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SRC = os.path.join(os.path.dirname(HERE), "runtime", "quant_data.sqlite")
DEFAULT_OUT = os.path.join(HERE, ".snapshot.sqlite")


def snapshot(src: str = DEFAULT_SRC, out: str = DEFAULT_OUT) -> str:
    if not os.path.exists(src):
        raise SystemExit(f"源库不存在：{src}")
    started = time.time()
    source = sqlite3.connect(src)
    target = sqlite3.connect(out)
    try:
        source.backup(target)
        target.execute("PRAGMA journal_mode=WAL")
        target.commit()
    finally:
        target.close()
        source.close()
    size_mb = os.path.getsize(out) // 1024 // 1024
    print(f"snapshot -> {out}  ({size_mb} MB, {time.time() - started:.0f}s)")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args()
    snapshot(a.src, a.out)
