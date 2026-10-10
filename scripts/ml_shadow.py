"""多因子模型影子留痕的周任务入口（计划任务 LynxAgent-MLShadow，周一到周五 17:30）。

python scripts/ml_shadow.py            # 记最近一个收完的周的名单（已记过就跳过），并刷新成绩文件
python scripts/ml_shadow.py --eval     # 看留痕至今的样本外成绩
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quantcore.quant.ml_shadow import BOOKS, evaluate, run_weekly  # noqa: E402

DB = ROOT / "runtime" / "quant_data.sqlite"
EVAL_JSON = ROOT / "runtime" / "ml_shadow_eval.json"   # 「模型选股」页直接读，现算要十几秒

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", action="store_true")
    a = ap.parse_args()
    if a.eval:
        for table in BOOKS:
            r = evaluate(DB, table)
            r.pop("rows", None)
            print(table, json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(run_weekly(DB), ensure_ascii=False))
        EVAL_JSON.write_text(json.dumps({t: evaluate(DB, t) for t in BOOKS}, ensure_ascii=False), encoding="utf-8")
