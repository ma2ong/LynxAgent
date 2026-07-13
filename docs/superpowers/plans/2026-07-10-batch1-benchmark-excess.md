# 批次 1：复盘基准超额 + 统计防污染 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 复盘统计从「绝对收益」升级为「绝对 + 相对全市场中位的超额收益」双口径，并用市场覆盖率守卫防止残缺日线污染统计。

**Architecture:** 全部改动集中在 `LocalQuantStore.evaluate_picks`（一次性构建市场收盘矩阵 → 每条留痕算超额 → 覆盖率不足的目标日整体记为未就绪）+ Review 页展示。API 端点零改动（透传新增字段）。

**Tech Stack:** Python/sqlite3（后端）、Vue3 + Element Plus（前端）、pytest。

对应 spec：`docs/superpowers/specs/2026-07-10-effectiveness-usability-design.md` 批次 1。

---

### Task 1: evaluate_picks 增加市场基准超额与覆盖率守卫

**Files:**
- Modify: `quantcore/quant/local_store.py:413-486`（evaluate_picks）
- Test: `tests/test_picks_history.py`

- [ ] **Step 1: 写失败测试（超额计算 + 覆盖率守卫）**

在 `tests/test_picks_history.py` 末尾追加（沿用文件内已有 `store` fixture、`_trading_dates`、`_seed_kline`）：

```python
def test_evaluate_picks_excess_vs_market_median(store):
    """超额收益 = 个股 T+N 收益 − 同期全市场中位收益。"""
    # 市场由 5 只股票构成：1 只大涨、1 只大跌、3 只横盘 → 中位 = 0
    up = [10.0 * 1.02 ** i for i in range(8)]
    down = [10.0 * 0.98 ** i for i in range(8)]
    flat = [10.0] * 8
    dates = _seed_kline(store, "600001", up)
    _seed_kline(store, "600002", down)
    for sym in ("600003", "600004", "600005"):
        _seed_kline(store, sym, flat)
    conn = store._conn()
    conn.execute(
        "INSERT OR IGNORE INTO picks_history VALUES (?,?,?,?,?,?,?,?)",
        (dates[1], "pattern", "600001", "涨股", 88.0, up[1], 1, "金叉"),
    )
    conn.commit()

    stats = store.evaluate_picks(days=60)
    item = next(i for i in stats["items"] if i["symbol"] == "600001")
    expected_ret = (1.02 ** 3 - 1) * 100
    assert item["t3"] == pytest.approx(expected_ret, abs=0.05)
    # 市场中位 = 横盘股 0% → 超额 ≈ 绝对收益
    assert item["excess_t3"] == pytest.approx(expected_ret, abs=0.05)
    pat = next(p for p in stats["pools"] if p["pool"] == "pattern")
    t3 = pat["horizons"]["t3"]
    assert t3["excess_win_rate"] == pytest.approx(1.0)
    assert t3["avg_excess"] == pytest.approx(expected_ret, abs=0.05)


def test_evaluate_picks_low_coverage_day_not_ready(store):
    """T+N 目标日市场覆盖率 <60% 时该 horizon 记为未就绪（不污染统计）。"""
    flat = [10.0] * 8
    dates = None
    for sym in ("600011", "600012", "600013", "600014", "600015",
                "600016", "600017", "600018", "600019", "600020"):
        dates = _seed_kline(store, sym, flat)
    conn = store._conn()
    # 制造缺口：最后一个交易日仅保留 2/10 只股票的日线（20% < 60%）
    conn.execute(
        "DELETE FROM daily_kline WHERE date = ? AND symbol NOT IN ('600011','600012')",
        (dates[-1],),
    )
    conn.execute(
        "INSERT OR IGNORE INTO picks_history VALUES (?,?,?,?,?,?,?,?)",
        (dates[-2], "smart", "600011", "留痕股", 80.0, 10.0, 1, ""),
    )
    conn.commit()

    stats = store.evaluate_picks(days=60)
    item = next(i for i in stats["items"] if i["symbol"] == "600011")
    # 600011 自身在缺口日有 bar，但市场截面残缺 → t1 必须为 None
    assert item["t1"] is None and item["excess_t1"] is None
    smart = next(p for p in stats["pools"] if p["pool"] == "smart")
    assert smart["horizons"]["t1"]["samples"] == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_picks_history.py -v`
Expected: 两个新测试 FAIL（KeyError: 'excess_t3' / t1 非 None），旧 4 个 PASS。

- [ ] **Step 3: 实现 evaluate_picks 改动**

`quantcore/quant/local_store.py`：模块顶部常量区（class 外、import 后）加：

```python
# 复盘统计的市场覆盖率守卫：T+N 目标日的日线覆盖数低于窗口内峰值的 60% 时，
# 视为数据未就绪（同步缺口），该 horizon 不进统计。
MIN_MARKET_COVERAGE = 0.6
```

`evaluate_picks` 内，`picks = conn.execute(...).fetchall()` 之后、`kline_cache` 之前插入市场矩阵构建：

```python
        import statistics

        # ---- 全市场收盘矩阵：基准（中位收益）与覆盖率守卫共用 ----
        by_date: Dict[str, Dict[str, float]] = {}
        for d, sym, c in conn.execute(
            "SELECT date, symbol, close FROM daily_kline WHERE date >= ? AND amount > 0",
            (since,),
        ):
            by_date.setdefault(str(d), {})[str(sym)] = _f(c)
        trading_dates = sorted(by_date)
        max_coverage = max((len(v) for v in by_date.values()), default=0)

        def _cover_ok(d: str) -> bool:
            return max_coverage <= 0 or len(by_date.get(d, {})) >= max_coverage * MIN_MARKET_COVERAGE

        bench_cache: Dict[tuple, Optional[float]] = {}

        def _bench(d0: str, d1: str) -> Optional[float]:
            """d0→d1 全市场中位涨跌幅（%），仅统计两日都有 bar 的股票。"""
            key = (d0, d1)
            if key not in bench_cache:
                m0, m1 = by_date.get(d0, {}), by_date.get(d1, {})
                rets = [(m1[s] / m0[s] - 1) * 100 for s in m0.keys() & m1.keys() if m0[s] > 0]
                bench_cache[key] = round(statistics.median(rets), 4) if rets else None
            return bench_cache[key]
```

主循环内改 T+N 计算（替换原 `rets` 段）：

```python
            i_mkt = bisect.bisect_right(trading_dates, str(pick_date)) - 1
            rets: Dict[str, Optional[float]] = {}
            for h in horizons:
                j = idx + h
                jm = i_mkt + h
                tgt = trading_dates[jm] if 0 <= i_mkt and jm < len(trading_dates) else None
                ready = (idx >= 0 and base > 0 and j < len(bars)
                         and tgt is not None and _cover_ok(tgt))
                if ready:
                    ret = round((_f(bars[j][1]) / base - 1) * 100, 2)
                    rets[f"t{h}"] = ret
                    bench = _bench(trading_dates[i_mkt], tgt)
                    rets[f"excess_t{h}"] = round(ret - bench, 2) if bench is not None else None
                else:
                    rets[f"t{h}"] = None
                    rets[f"excess_t{h}"] = None
```

聚合桶同时收集超额（`bucket` 段改为）：

```python
            bucket = agg.setdefault(str(pool_name), {h: [] for h in horizons})
            ex_bucket = ex_agg.setdefault(str(pool_name), {h: [] for h in horizons})
            for h in horizons:
                v = rets[f"t{h}"]
                if v is not None:
                    bucket[h].append(v)
                ev = rets[f"excess_t{h}"]
                if ev is not None:
                    ex_bucket[h].append(ev)
```

（`ex_agg: Dict[str, Dict[int, List[float]]] = {}` 与 `agg` 同处初始化。）

池统计输出（`stats[f"t{h}"]` 字典）追加两个键：

```python
                evals = ex_agg[pool_name][h]
                stats[f"t{h}"] = {
                    "samples": len(vals),
                    "win_rate": round(sum(1 for v in vals if v > 0) / len(vals), 4) if vals else None,
                    "avg_return": round(sum(vals) / len(vals), 2) if vals else None,
                    "excess_win_rate": round(sum(1 for v in evals if v > 0) / len(evals), 4) if evals else None,
                    "avg_excess": round(sum(evals) / len(evals), 2) if evals else None,
                }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_picks_history.py -v`
Expected: 全部 PASS（含旧测试——旧测试的市场就是它们种的 2~10 只票，覆盖率满足守卫）。

- [ ] **Step 5: 真实数据冒烟**

Run: `python -c "import sys; sys.path.insert(0, '.'); from quantcore.quant.local_store import LocalQuantStore; import json; r = LocalQuantStore().evaluate_picks(days=30); print(json.dumps(r['pools'], ensure_ascii=False, indent=1))"`
Expected: 每池每 horizon 出现 `excess_win_rate` / `avg_excess`；受 7-09 覆盖缺口影响的样本数比改动前减少（如 7-08 留痕的 t1 被守卫置空）。

- [ ] **Step 6: Commit**

```bash
git add quantcore/quant/local_store.py tests/test_picks_history.py
git commit -m "feat(review): market-median excess returns and coverage guard in picks evaluation"
```

### Task 2: Review 页展示超额口径

**Files:**
- Modify: `frontend/src/api/quant.ts:478-501`（类型）
- Modify: `frontend/src/views/Review/Index.vue`

- [ ] **Step 1: 类型扩展**

`PicksHorizonStat` 加两个可空字段、`PicksStatsItem` 加三个可空字段：

```ts
export interface PicksHorizonStat {
  samples: number
  win_rate: number | null
  avg_return: number | null
  excess_win_rate?: number | null
  avg_excess?: number | null
}
```

```ts
  t1: number | null
  t3: number | null
  t5: number | null
  excess_t1?: number | null
  excess_t3?: number | null
  excess_t5?: number | null
```

- [ ] **Step 2: 池卡片加超额行**

`Review/Index.vue` 的 `.horizon-cell` 内、`samples` 行之前插入：

```html
            <small :class="retClass(stat(p, h.key)?.avg_excess)">
              超额 {{ fmtExcess(stat(p, h.key)?.avg_excess) }} · 胜 {{ fmtRate(stat(p, h.key)?.excess_win_rate) }}
            </small>
```

script 增加：

```ts
const fmtExcess = (v: number | null | undefined) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}pp`)
```

- [ ] **Step 3: 明细表加口径切换**

`panel-head` 的池筛选后加口径切换（新 ref `retMode`）：

```html
        <el-radio-group v-model="retMode" size="small">
          <el-radio-button value="abs">绝对</el-radio-button>
          <el-radio-button value="excess">超额</el-radio-button>
        </el-radio-group>
```

T+N 列改为按口径取值：

```html
        <el-table-column v-for="h in horizons" :key="h.key" :label="h.label" width="90">
          <template #default="{ row }">
            <span :class="retClass(cellVal(row, h.key))">{{ fmtRet(cellVal(row, h.key)) }}</span>
          </template>
        </el-table-column>
```

```ts
const retMode = ref<'abs' | 'excess'>('abs')
const cellVal = (row: PicksStatsItem, key: 't1' | 't3' | 't5') =>
  retMode.value === 'abs' ? row[key] : (row as any)[`excess_${key}`]
```

- [ ] **Step 4: 口径说明更新**

`foot-note` 文案替换为：

```
口径说明：留痕价为扫描当时的价格；T+N 为留痕后第 N 个交易日收盘价相对留痕价的涨跌幅；
超额 = 个股收益 − 同期全市场中位收益（单位 pp），用于区分策略能力与大盘涨跌；
目标日行情覆盖不足（数据同步缺口）的样本自动排除。历史表现不代表未来收益，不构成投资建议。
```

页头副标题（`review-head p`）改为：

```
每次扫描自动留痕，按真实行情统计各池 T+1 / T+3 / T+5 胜率与相对大盘的超额——数据说话，不承诺胜率。
```

- [ ] **Step 5: 构建验证**

Run: `cd frontend && npx vue-tsc --noEmit && npm run build`
Expected: 类型检查与构建通过。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/quant.ts frontend/src/views/Review/Index.vue
git commit -m "feat(review): show excess-return metrics and abs/excess toggle on review page"
```

### Task 3: 实机巡检

- [ ] **Step 1: 确认后端/前端存活**（后端 8001 不在则以被跟踪后台命令起 `python -m uvicorn app.lite_main:app --port 8001`，PowerShell；前端 5173 vite）
- [ ] **Step 2: headless Playwright 巡检 `/review`**（API 登录 looptest/loop-test-1234 拿 token 注入 localStorage `auth-token`，访问 `http://[::1]:5173/review`，goto 用 domcontentloaded）：断言页面出现「超额」字样、池卡片渲染、切换「超额」口径后明细表数值变化、无 console error。
- [ ] **Step 3: 全量回归** `python -m pytest tests -q` 全绿。
- [ ] **Step 4: Commit（如巡检产生修复）并在本计划文件勾选完成项。**
