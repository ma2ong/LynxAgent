# KOL 日报真实数据源接入设计（X 优先 + 雪球/微博补充）

日期：2026-06-29 ·  状态：已确认，待实现  ·  代码库：lynxagent

## 目标

给「KOL 日报」页接真实采集源，替换占位 mock。X(推特) 为主，雪球/微博为补充，三源汇总后由 DeepSeek 按个股聚合，落盘 `runtime/kol_digest.json`，后端 `get_digest()` 读取（新鲜则用真实数据，否则降级占位）。

成功标准：
- `scripts/build_kol_digest.py` 能从 X / 雪球 / 微博三源采集，归一为统一结构喂给现有聚合逻辑。
- 每条观点的来源 `source` 带**真实平台 + 作者 + 原文链接**（修掉当前硬编码 `platform:"X"` 的 bug）。
- 名单为空时退化为纯热门兜底，照样产出 digest。
- 前端 `KolDigest` 页与 `get_digest()` 契约零改动。

## 现状（已建好的部分）

- `quantcore/quant/kol_rooms.py`：`get_digest()` 读 `runtime/kol_digest.json`（36h 新鲜度），无/过期降级 `_MOCK_DIGEST`。**契约固定，本设计不改。**
- `scripts/build_kol_digest.py`：已有 X-only 脚手架——`twitter search` 采集 → DeepSeek `aggregate()` 按个股聚合 → `serenity_resolve.resolve_beneficiaries` 解析代码 → 落盘。
- 已知 bug：`aggregate()._sources()` 硬编码 `{"platform": "X"}`，接入雪球/微博后会把来源错标成 X。

> 注：`kol_rooms.py`(M) 与 `scripts/`(untracked) 是本功能的未提交 WIP，归本分支。

## 关键决策（已与用户确认）

1. **采集策略 = 混合**：热门兜底（免维护名单、立即可跑）+ 名单增强（用户给 handle 后叠加）。
2. **X 名单也扩**：X 名单抓取从 `twitter search from:<h>` 升级为 `twitter tweets <handle>`（直抓 KOL 时间线，更准更全），与微博 `user-posts` 对齐。
3. **handle 来源 = 发现辅助 + 用户拍板**：新增 `--discover` 模式按关键词搜出高互动作者候选表供用户挑选，handle 全为真实抓取，绝不编造。

## 数据源命令映射（opencli，字段已实测）

| 平台 | 热门兜底 | 名单增强 | 归一字段 |
|---|---|---|---|
| X | `twitter search <词> --filter live` | `twitter tweets <handle>` | text / author / url / likes |
| 雪球 | `xueqiu hot` | `xueqiu feed`（网页关注的人时间线） | author / text / likes / url |
| 微博 | `weibo search <财经词>`（`title`→text，无 likes 记 0） | `weibo user-posts <@博主>`（字段最全） | text / author / url / likes |

说明：
- 雪球无「指定用户发帖」命令，名单增强 = 用户在 xueqiu.com 关注目标博主后走 `feed`；脚本侧雪球无 handle 配置。
- 微博 `hot` 仅 rank/url、无正文作者，**不用**。
- live 抓取依赖 Chrome 已登录对应站点 + Browser Bridge 扩展（运行前置条件，写入脚本头注释与 README）。

## 架构与改动（全在 `scripts/build_kol_digest.py`，+ prompt 一行）

### 1. 通用 opencli 调用器
`_opencli_json(args: list[str]) -> list[dict]`：由现有 `_search()` 重构而来——写临时文件再读（避免 stdin 管道破坏 JSON），统一 `-f json` + 超时 + 失败 warn 返回 `[]`。所有采集器复用。

### 2. 三个采集器（每源 try/except 独立，一个挂不影响其他）
- `_collect_x() -> list[item]`：`SEARCH_TERMS` 走 search（兜底）+ `X_KOLS` 各走 `twitter tweets`（名单）。platform="X"。
- `_collect_xueqiu() -> list[item]`：`xueqiu hot`（兜底）+ `xueqiu feed`（名单）。platform="雪球"。
- `_collect_weibo() -> list[item]`：`FINANCE_KEYWORDS` 走 `weibo search`（兜底，`title`→text、likes=0）+ `WEIBO_KOLS` 各走 `weibo user-posts`（名单）。platform="微博"。

### 3. 归一与汇总
- `_norm(platform, raw) -> {platform, author, text, url, likes}`：字段兜底（缺 text 用 title；缺 likes→0；缺 url→""）。
- `collect()`：汇总三源 → 跨源去重（key=`(platform, url or id or hash(author+text[:40]))`）→ 短文本过滤（<8 字丢弃）→ 按 likes 粗排 → 取前 `MAX_ITEMS`。

### 4. 修来源平台 bug
`_sources(idxs)` 改为读每条 item 真实的 `item["platform"]`（不再硬编码 "X"）。输出 `sources_platform` 改为实际出现平台的并集。

### 5. 配置块
```
SEARCH_TERMS      # X/微博 关键词兜底（可共用）
FINANCE_KEYWORDS  # 微博搜索词（默认复用 SEARCH_TERMS）
X_KOLS: list[str]      # 推特 handle（twitter tweets）
WEIBO_KOLS: list[str]  # 微博 @博主（weibo user-posts）
SEARCH_LIMIT / MAX_ITEMS
```
雪球名单靠网页关注，无脚本配置。全部留空 → 纯热门兜底。

### 6. `--discover` 模式（独立路径，不调 LLM、不写 digest）
- 按 `SEARCH_TERMS`/`FINANCE_KEYWORDS` 跑 `twitter search` + `weibo search` → 按作者聚合（出现次数、累计赞）。
- 打印候选表 `handle · 平台 · 次数 · 累计赞 · 样例文`，并写 `runtime/kol_candidates.json` 供留存。
- 用户眼过后把认可的真实 handle 复制进 `X_KOLS`/`WEIBO_KOLS`。粉丝数为可选 best-effort（仅在低成本时补，否则省略）。

### 7. LLM prompt
`_agg_prompt` 一处措辞「从推特(X)采集的中文财经推文」→「从 X/雪球/微博 采集的中文财经内容」，其余 prompt、约束、JSON schema 不动。

## 验证

- **离线单测**（不联网，可入 CI）：构造三平台合成 item，断言 ① `platform` 正确透传到每条 `source`；② 跨源去重生效；③ `collect()`→`aggregate()` 输出结构匹配 `get_digest()` 契约（stats/hottest/attention_rank/stocks[].view_blocks[].sources/other_topics 字段齐全）。LLM 部分用 monkeypatch 注入固定 JSON，避免真调模型。
- **联网冒烟**（用户本地，需登录）：逐源各单跑一条 opencli 命令确认真实 JSON 字段未漂移；跑 `--discover` 看候选表；跑默认模式生成一次真 digest，前端页核对来源平台/链接正确。
- 任一源失败 → 优雅降级（warn + 跳过），整体仍出结果或保留上次/占位。

## 不做（YAGNI）

- 定时调度（仍本地手动跑；需要时用户自挂 Windows 计划任务）。
- 前端改动、`get_digest()` 契约改动、`_MOCK_DIGEST` 占位改动。
- 雪球 `comments <个股>` 逐股抓取（hot+feed 已覆盖；后续可加）。
- handle 粉丝数强制采集（profile 调用成本高，仅 best-effort）。

## 风险

- opencli live 抓取强依赖 Chrome 登录态；未登录时对应源静默返回空，由热门兜底/降级吸收。
- 微博 `search` 无 likes，粗排时该源条目 likes=0 会偏后；可接受（名单增强的 user-posts 有 likes）。
- DeepSeek 聚合质量取决于采集文本信号；信号不足（<5 条）时脚本放弃生成、保留上次/占位（现逻辑已有）。
