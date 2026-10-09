"""多因子模型影子留痕的周任务入口（计划任务 LynxAgent-MLShadow，周六 03:00）。

python scripts/ml_shadow.py            # 记本周名单（周末才记）
python scripts/ml_shadow.py --eval     # 看留痕至今的样本外成绩
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quantcore.quant.ml_shadow import evaluate, run_weekly  # noqa: E402

DB = ROOT / "runtime" / "quant_data.sqlite"

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--force", action="store_true", help="周中也记（只用于手工补记）")
    a = ap.parse_args()
    if a.eval:
        r = evaluate(DB)
        r.pop("rows", None)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(run_weekly(DB, force=a.force), ensure_ascii=False))
