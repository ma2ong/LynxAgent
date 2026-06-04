---
name: quant-lite-clean-repo
description: "LynxAgent — the clean, legally-safe repo for Allen's lite quant SaaS (at C:\\Users\\Administrator\\lynxagent)"
metadata: 
  node_type: memory
  type: project
  originSessionId: f0cd7f38-3baa-46a6-ba28-a9e556a9c2cf
---

Clean repo at `C:\Users\Administrator\lynxagent` (folder renamed quant-lite→alphaagent→lynxagent over 2026-06-01) for Allen's lite quant SaaS, escaping the proprietary `app/`+`frontend/` of the main repo ([[licensing-fork-origin]]).

**Brand name = LynxAgent** (FINAL, chosen 2026-06-01; tagline "See the patterns others miss."). Applied everywhere: GitHub repo (https://github.com/ma2ong/LynxAgent), LICENSE/NOTICE/README/index.html/Login.vue, package ids `lynxagent`/`lynxagent-frontend`, local folder. lynx = sharp-eyed pattern spotter. Domains: lynxagent.com taken, **lynxagent.ai + .io available** (Allen to register). AlphaAgent was rejected 2026-06-01 after trademark/domain check: descriptive + no domains + direct namesakes in the identical quant-multi-agent space (GitHub RndmVariableQ/AlphaAgent, LLMQuant/Alpha-Agent, BlackRock "AlphaAgents" paper). Also explored pinyin options (盘灵/形眼/选鹰 had .ai+.cn free) but Allen went with LynxAgent. ⚠️ Trademark NOT formally cleared (CNIPA needs captcha/agent); LynxAgent's profile is far cleaner than AlphaAgent's but still verify before heavy investment.

**Python import package = `quantcore`** (renamed 2026-06-01 from `tradingagents` to drop the fork-origin name; all 27 refs across app/+core updated, entrypoint `app.lite_main` import-verified, committed 237d841). Brand-neutral on purpose so a future brand change won't require another package rename.

**Verified code ownership (2026-06-01, per-file git authorship + content diff):**
- The core package (folder now `quantcore/`, formerly `tradingagents/`) is a **45-file subset, 44 authored by ma2ong** (his own quant/analysis/data/trading modules) — NOT the Apache upstream bulk. The upstream framework dirs (`agents/`, `graph/`, `dataflows/`, `llm_adapters/`, `config/`, `utils/`…) were deliberately NOT copied in, and nothing imports them → repo is self-contained, does not redistribute the Apache TradingAgents Work.
- The only non-Allen-origin files were scaffolding: `app/core/database.py` (50-line Mongo connector), `app/__init__.py`, `app/routers/__init__.py`, `frontend/src/{App.vue, main.ts, api/request.ts}`. **All rewritten clean on 2026-06-01** (public APIs preserved: `get_mongo_db`/`close_database`/`MONGO_URI`/`MONGO_DB`; `ApiClient`/`request`/`ApiResponse`/`RequestConfig`). `tradingagents/__init__.py` had already been Allen-rewritten earlier (content his; git shows hsliuping only because the path predates him).
- After the rewrite, no substantive hsliuping code remains; the "all original work" claim is defensible.

**Licensing files:** `LICENSE` = **PROPRIETARY, © 2026 Allen Ma / 深圳迈彩视觉有限公司, all rights reserved** (not Apache). `NOTICE` claims "all source original, no third-party source code." Allen chose (2026-06-01) to keep NOTICE as-is — NOT to add a TradingAgents/TauricResearch fork-lineage acknowledgment.

Status (2026-06-01): backend py-compile OK; frontend **build-verified**. Committed + pushed to **https://github.com/ma2ong/LynxAgent** (`main`; node_modules/dist/.env/db gitignored). git user ma2ong; cred helper = GCM ("manager"); gh authed as ma2ong.

**Frontend is now a multi-page app** (commit 3bdc5f3, 2026-06-01): clean sidebar `components/Layout/AppLayout.vue` + nested router, 6 pages — 智能选股 (`Quant/index.vue`, has tabs 一键推荐/形态智选/数据同步/单股量化/策略回测/因子研究), A股热点 (`Insights/HotNews.vue`, copied from main repo — Allen's own), 利好监控 (`Insights/CatalystMonitor.vue`, copied), 个股深研 (`Analysis/SingleAnalysis.vue`, NEW), 我的自选股 (`Favorites/index.vue`, NEW), 模拟交易 (`PaperTrading/index.vue`, NEW). The 3 NEW pages were built fresh (NOT copied from hsliuping) against Allen's existing lite backend endpoints `/api/analysis/single`, `/api/favorites/*`, `/api/paper/*`. Local preview: backend `uvicorn app.lite_main:app --port 8000`, frontend `npm run dev` (5173, proxies /api→8000).

**Bug fixes (2026-06-01, commits af9cf3d + c3f29b5):**
- **个股深研 race crash fixed**: `LiteDeepAnalysisLLM.chat` (in lite_main.py) keyed its canned responses by call-order, but `DeepAnalysisFramework.analyze` fires the 6 prompts CONCURRENTLY (ThreadPoolExecutor) → industry prompt sometimes got the risk JSON-array response → `json.loads`→list → `parsed.get` → `'list' object has no attribute 'get'` → whole deep analysis degraded. Fix: dispatch chat() by **prompt content** (产业链/打分/投资风险/跟踪计划/综合评级), not call_count; + isinstance(dict) guard in `IndustryAnalystAgent.analyze`. Now deep analysis returns deep_rating + multi-agent output. SingleAnalysis.vue also cleaned: shows `overall_score` (was '-'), whitelists 7 narrative sections, hides metadata keys, shows degraded banner instead of dumping JSON.
- **数据同步 (full-market local kline sync) fixed**: backend `/api/lite/datalake/sync` reads `full` from QUERY but frontend `syncMarket` sent it in the BODY → 全量同步 silently ran incremental. Fixed to query param. `startSync` was fire-and-forget with no feedback → now toasts start/completion + immediate progress. The sync IS healthy (background thread, ~5525 stocks, Tencent daily kline primary→akshare fallback, writes local SQLite via `quantcore/quant/sync_service.py` + `local_store`). 形态智选 needs this local data ("本地数据未就绪" banner until a full sync completes). NOTE: separate from the 读取股票池/同步数据湖 panel which writes MongoDB (unavailable in lite → inserted:0).
- **Stable login across restarts**: added `load_dotenv()` to lite_main.py top (before lite_auth import) + created gitignored `.env` with a fixed `JWT_SECRET` (also SAAS_LITE_ADMIN_USERNAME/PASSWORD=admin/admin123). Previously JWT_SECRET was random per start → every backend restart invalidated all tokens (401s). Default login: **admin / admin123**.

**P0 technical indicators shipped (2026-06-02, commit 77ee383):** added ATR(14 Wilder), KDJ(9,3,3), ADX/+DI/-DI(14), Chandelier-Exit stop to `quantcore/quant/factors.py` (`indicator_snapshot()`/`latest_adx()`); engine attaches them to `analyze()` latest; pattern scan applies an ADX trend-strength tilt (+4 if ADX≥25, −4 if <20) and shows "ADX N" in reasons; deep research shows KDJ/ADX/ATR in technical analysis + an ATR/Chandelier adaptive stop in the operation advice. All **clean-room from public formulas** — these were inspired by an inventory of github.com/cinar/indicator (a Go TA lib) but that repo is **AGPL-3.0**, so its code/MCP server must NOT be used or "ported"; only standard public indicator math was implemented. **P1 shipped (2026-06-02, commit 4336e51):** OBV, MFI(14), CMF(20), Keltner Channel in factors.py; `keltner_breakout` strategy registered; deep research shows 资金流 MFI/CMF reading.

**Composable strategy framework shipped (2026-06-02, commit a8dcd52):** `strategies.resolve_strategy()` + `_combine()` merge multiple strategies via AND/OR/majority; `backtest.py` adds a stateful stop-loss (exit on close ≤ entry×(1−pct)); `engine.backtest` + `/api/quant/backtest` accept `strategies[]`/`combine`/`stop_loss_pct` (composites & stop-loss run on the vector engine, backward-compatible with single `strategy`); backtest tab UI is multi-select + combine mode + stop %. Also FIXED a latent crash: factors.py used `pd.NA` in denominators which makes columns object-dtype and crashes `rolling().sum()` ("No numeric types to aggregate") on zero-range/limit-up bars — switched all to `float('nan')`.

**P2 shipped (2026-06-02, commit 5eaace4):** CCI(20), Williams %R(14), StochRSI(14) as vectorized columns in factors.py; Aroon(25) computed at-latest in `indicator_snapshot` (O(period), no rolling.apply in the scan hot path); deep research shows a 动量补充 line. **Indicator roadmap complete** (P0 ATR/KDJ/ADX + P1 OBV/MFI/CMF/Keltner + composite strategy framework + P2 momentum extras), all inspired by cinar/indicator's feature list but clean-room from public formulas (cinar is AGPL — never used/ported).

**Indicator visualization shipped (2026-06-02, commit 77cb07e):** `/api/quant/kline` payload (chart_service.py) now carries `kdj{k,d,j}`, `adx{adx,plus_di,minus_di}`, `moneyflow{mfi,cmf}` series; `KLineProChart.vue` has toggleable sub-panels (KDJ / ADX-DMI / 资金流-MFI) with dynamic ECharts grid layout (volume+MACD always on, container height grows with enabled panels). Deep research text also now reports StochRSI + OBV trend. So indicators are visible in BOTH 个股深研 text and the K-line chart sub-panels (chart appears in screening 看图 drawer + stock detail).

Latest commit: 77cb07e. Backend runnable; all indicators + composite backtest + chart sub-panels verified working live.

**Gotcha saved:** in pandas, `Series.replace(0, pd.NA)` before a `rolling().sum()`/`.agg` turns the column object-dtype → DataError "No numeric types to aggregate". Use `float('nan')` to stay float64.

**Verification gotcha (cost hours 2026-06-01/02):** on this Windows env, piping `curl` JSON into `python -c "json.load(sys.stdin)"` mangles non-ASCII and gave FALSE negatives on substring checks (e.g. `'ADX' in text` returned False even though the field contained it). Always write the response to a file and read with `open(path, encoding='utf-8')` when verifying API output that contains Chinese.

---

## 2026-06-03～06-04 新增功能（已 push 到 origin/main）

**大盘情绪仪表盘（commit a1d10ce）：** `/market/sentiment` 页面。`quantcore/quant/market_sentiment.py` + `/api/lite/market-sentiment` 端点。KPI 卡（情绪温度/成交额/连板高度/涨跌比）+ 4 张 ECharts 图表（成交额趋势/5日线占比/涨跌停分布/连板梯队）。数据基于本地日线 + 实时行情快照，计算量大，加载约 5-10 秒属正常。

**涨停热点（commit 20f9438 + 改进 1538b4d + 4572702）：** `/limit-up` 页面。`quantcore/quant/limit_up.py` + `/api/lite/limit-up` 端点。连板梯队 × 概念板块矩阵，按日期查询，今日默认。`REASON_BY_NAME` dict 预置了主要热门股的涨停逻辑说明；`CONCEPT_ORDER` 定义展示顺序；`_limit_reason()` 生成兜底模板文案。

**个股 AI 研报（commit 355147c）：** `/stock-report` 页面。输入股票代码点「生成研报」，后端走 `/api/quant/report`。页面属等待触发型，首次加载内容区为空是正常行为。

**Datalake 健康端点 + 智能选股健康横幅（commit cae78bc，2026-06-04）：**
- 后端新增 `/api/lite/datalake/health`（`app/lite_main.py`末尾），含 auto-start 逻辑（30 分钟冷却防抖）
- `quantcore/quant/local_store.py` 新增 `kline_health()` → 返回 ready/fresh/stale/empty 状态 + message
- `quantcore/quant/sync_service.py` 代码质量提升（变量名清晰化，英文 docstring，progress dict 格式化）
- `frontend/src/views/Quant/index.vue` 顶部新增健康横幅（绿色"今日已更新"/橙色"同步中 N/M"/红色"数据不足"），替代原来的 "本地数据未就绪" 静态提示
- `frontend/src/api/quant.ts` 新增 `QuantDataHealth` 类型 + `dataHealth` / `startSync` / `refreshDataHealth` API 调用

**当前数据状态（2026-06-04 验证）：** SQLite `quant_data.sqlite` 已有 5512 只股票的日线，最新完整交易日 2026-06-04（5208/5525 只），`health.ready = true`，`status = "fresh"`。增量同步在盘中自动触发。

**Sync 与 TradingAgents 的关系（2026-06-04 核查）：**
- LynxAgent 是写作源头，TradingAgents 是下游同步目标（不反向）
- 两仓库共用 `app/lite_main.py` + `frontend/src/views/`（内容一致，仅 import 路径 `tradingagents/` vs `quantcore/` 不同）
- 最新 commit 内容已完全一致；TradingAgents 保持 private 且不需要向 LynxAgent 回同步

**商业化确认（2026-06-04）：** 全部代码为 Allen 自己原创，已在 06-01 逐文件核查，无 hsliuping 实质性代码残留。`LICENSE` = 专有许可证（© 2026 Allen Ma），商业化合法可行。

**当前最新 commit：** `cae78bc feat(quant): datalake health endpoint + smart-selection health banner`
