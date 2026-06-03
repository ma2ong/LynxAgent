"""多 Agent 选股流水线 (功能B)。

stage 一句话职责：
- feedback_curator: 读历史反向优汰规则，产出本轮因子偏好/回避标签。
- theme_agent:      线①，财经新闻 -> 热点主题/板块 + 热度（LLM 或词频降级）。
- strategy_pick:    线②，几十个高胜率策略库满足量化因子即入选（复用已有 signal 函数）。
- sources:          money_follow/market/contrarian/retail_sentiment 四路轻量选股 + 数据源。
- critic:           对去重候选 LLM 打分 0-10 + 拒绝理由（无 LLM 用 composite_score 规则降级）。
- orchestrator:     串联各 stage、第一层排除、多路汇总去重、落盘 pipeline_runs/<ts>/steps/*.json。
- t5_feedback:      反向优汰闭环，读历史 picks 的 T+5 涨跌，反向总结因子规则写回 feedback。

HTTP 路由建议片段见 orchestrator.py 顶部 docstring（本包不写路由）。
"""
from .orchestrator import get_run, list_runs, quick_critic_batch, run_pipeline
from .t5_feedback import run_t5_review

__all__ = ["run_pipeline", "list_runs", "get_run", "run_t5_review", "quick_critic_batch"]
