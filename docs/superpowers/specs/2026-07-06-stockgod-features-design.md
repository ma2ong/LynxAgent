# 借鉴 stockgod.xyz 功能升级设计（A股适配）

日期：2026-07-06 · 状态：已获 Allen 确认 · 范围：本地运行，暂不含公网部署

## 背景与目标

参考 stockgod.xyz（美股 AI 选股站）的产品形态，把其 6 个核心功能借鉴到 LynxAgent 并适配 A 股。
盘点结论：项目已有一半地基（investor_panel 五人格打分、留痕胜率闭环、竞价/涨停/资金面板），
本次为 2 个全新模块（热力图、Arena）+ 4 个升级（盘报、宏观条、五方判读批量化、聪明钱）。

**成功标准**：每批次独立可验证（pytest + 前端构建 + 实机页面可用），做完一批验证一批再推进。

## 全局关键决策

1. **批量判读只针对候选池**（几十只/日），不扫全市场 5000 只 —— 控 LLM 成本。
2. **盘报定时自动生成**（挂现有 APScheduler），用户打开即看，不是点击触发。
3. **Arena 每日调仓一次**、收盘价结算 —— A股 T+1，盘中实时无意义。
4. 所有新数据落现有 `runtime/quant_data.sqlite`，不引入新存储。
5. LLM 走现有 `quantcore/quant/llm.py` 统一入口，无密钥时降级为纯规则/空态提示。

## 批次 1：盘报 + 宏观条

**盘报**（对标 stockgod /reports）
- 新模块 `quantcore/quant/report_daily.py`：
  - 盘前版（工作日 9:00）：复用竞价候选、催化剂、隔夜要点 → LLM 写「今日看点」。
  - 收盘版（工作日 15:30）：复用环境标签、涨停主线、资金面板数据 → LLM 写
    「一句话定调 / 主线分析 / 热门追踪 / 明日看点 / 核心结论」结构化复盘。
  - 结果存 SQLite 表 `daily_reports(date, kind, content_json, created_at)`，可翻历史。
  - LLM 不可用 → 存纯数据版（各板块原始要点列表），页面正常渲染。
- API：`GET /api/quant/reports?date=&kind=`、`GET /api/quant/reports/latest`。
- 前端：新页 `/reports`（盘前/收盘 tab + 日期切换），侧边栏入口。

**宏观条**（对标 stockgod 顶部指标条）
- 后端 `GET /api/quant/macro-bar`：上证/深成/创业板指数涨跌、涨跌家数、
  北向净流入（akshare，取不到则省略该项）、两市成交额；60s 缓存。
- 前端：`MacroBar.vue` 挂 `AppLayout` 顶部，全站可见；A股红涨绿跌。

## 批次 2：五方判读批量化

- 现有 `investor_panel.py` 单股打分保持不动；新增 `investor_panel_batch()`：
  对指定候选池（形态/智能/竞价池当日候选）批量打分，结果存表
  `panel_scores(date, symbol, persona, score, stance, reason)`，当日缓存、重复请求不重算。
- 触发方式：池扫描完成后后台任务打分（不阻塞扫描本身）。
- API：`GET /api/quant/panel/batch?pool=`。
- 前端：智能选股/形态列表加「五方均分 + 分歧度」列（可排序），
  点开弹层显示 5 人格各自评分与一句话理由。

## 批次 3：热力图

- 后端 `GET /api/quant/heatmap?level=industry|stock&industry=`：
  基于本地日线 + stock_meta 行业字段聚合，面积=市值（近似：成交额兜底）、颜色=当日涨跌幅。
  零 LLM 成本、零新数据源。
- 前端：新页 `/heatmap`，ECharts treemap，行业 → 点击下钻个股 → 点个股跳深研。

## 批次 4：Arena + 聪明钱

**Arena**（对标 stockgod /arena）
- 5 个 AI 人格（复用 investor_panel 五风格）各管 100 万虚拟资金。
- 每工作日收盘后：LLM 按人格方法论 + 当日候选池 + 持仓现状生成调仓指令
  （买/卖/持有 + 理由「判词」），按收盘价成交，含 A 股交易成本（复用 backtest 成本参数）。
- 表：`arena_portfolios`、`arena_trades`、`arena_nav(date, persona, nav)`。
- 前端：新页 `/arena`：排行榜（总资产/收益率/持仓数）+ 人格详情（持仓明细、判词、交易历史）。
- 结算复用留痕闭环的行情读取路径；LLM 不可用当日 → 全体持仓不动，NAV 照常结算。

**聪明钱**（对标 stockgod /whales，A股语境）
- 数据源 akshare：龙虎榜营业部席位（游资追踪）、基金季报重仓（共识持仓）、北向持股变动。
- **实施前先做数据质量验证**（可得性/延迟/字段完整度），不达标则砍掉对应子块。
- 前端：新页 `/smart-money`：席位活跃榜、基金共识重仓榜、加减仓热力。

## 测试与验证（每批通用）

- 后端：新模块 pytest（生成/降级/缓存路径）；LLM 调用 mock。
- 前端：vue-tsc + vite build 通过；Playwright 实机巡检新页面。
- 定时任务：手动触发入口（API `?force=1`）便于不等 cron 验证。

## 明确不做

- 公网部署（另立项目）；stockgod 的 ETF 专区（A股 ETF 需求另议）；
  盘中实时 tick 级数据；全市场批量 LLM 打分。
