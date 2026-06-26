"""KOL 日报（对标 themarketbrew /twitter-digest）的数据层 —— 当前为占位示例数据。

单页信息流，一进页面就平铺当天「重要且有效」信息：
  ① 头部统计（日期 + N 位 KOL · M 条内容）+ 今日最热
  ② 今日 KOL 关注度 TOP（哪些个股被最多 KOL 讨论）
  ③ 个股观点摘要卡（综述 + 多空综合分析 / 买入逻辑 / 卖出·风险，多 KOL 署名 + 来源）
  ④ 其他热议（宏观 / 赛道级，非单一个股）

⚠️ 重要：下方 _MOCK_DIGEST 为人工编造的占位示例，**非真实 KOL 观点**。
每条都带 source（平台/作者/链接），接真实源后即为真实出处；占位阶段 url 为空、平台标注以示区分。

============================ 真实数据源接入 TODO ============================
get_digest() 现返回占位数据。后续替换为真实采集：
  1. 数据源：A 股 KOL 多在 雪球(xueqiu) / 微博 / 公众号，推特(X)上较少。
     采集这些公开主页/时间线（RSS 或第三方抓取），保留每条原文 url 作为 source。
  2. 归整：用已接的 DeepSeek LLM 把当日帖子按个股聚合，分类为
     多空综合分析 / 买入逻辑 / 卖出·风险 / 数据·客观观察，抽取 stance / 涉及代码，
     并统计每只个股被多少 KOL 提及（关注度）与帖子数。
  3. 持久化：每日生成 digest 写入 mongo/sqlite，本函数按日期读。source 必须回填真实链接。
对外契约 get_digest() 与字段结构保持不变 —— 换真实源时前端零改动。
==========================================================================
"""
from __future__ import annotations

from typing import Dict


def _src(platform: str, author: str, url: str = "") -> Dict:
    """一条来源：平台 + 作者 + 原文链接（占位阶段 url 为空，接真实源后回填）。"""
    return {"platform": platform, "author": author, "url": url, "is_placeholder": not url}


_MOCK_DIGEST: Dict = {
    "is_mock": True,
    "date": "示例占位",
    # ① 头部统计（接真实源后为当日真实统计）
    "stats": {"kol_total": 26, "post_total": 58, "hours": 24},
    "hottest": {"code": "300308", "name": "中际旭创", "kol_count": 8},
    # ② 今日 KOL 关注度排行
    "attention_rank": [
        {"code": "300308", "name": "中际旭创", "kol_count": 8, "post_count": 14},
        {"code": "688256", "name": "寒武纪", "kol_count": 6, "post_count": 11},
        {"code": "300750", "name": "宁德时代", "kol_count": 5, "post_count": 7},
        {"code": "002253", "name": "川大智胜", "kol_count": 4, "post_count": 6},
        {"code": "600519", "name": "贵州茅台", "kol_count": 3, "post_count": 4},
    ],
    # ③ 个股观点摘要卡
    "stocks": [
        {
            "code": "300308", "name": "中际旭创", "group": "多头共识",
            "tag": "热议", "stance": "看多", "kol_count": 8, "post_count": 14,
            "summary": "多数 KOL 认为光模块需求未见顶，1.6T 放量是核心逻辑，分歧主要在估值。",
            "view_blocks": [
                {"kind": "多空综合分析", "count": 3,
                 "handles": ["@示例-产业链博主", "@示例-宏观博主"],
                 "content": "多头一致看好 1.6T 导入节奏，空头主要担忧估值与北美 capex 指引波动。",
                 "sources": [_src("雪球", "示例-产业链博主"), _src("微博", "示例-宏观博主")]},
                {"kind": "买入逻辑", "count": 4,
                 "handles": ["@示例-产业链博主"],
                 "content": "龙头份额稳固，1.6T 业绩弹性大，海外客户需求确定性高。",
                 "sources": [_src("雪球", "示例-产业链博主")]},
                {"kind": "卖出 / 风险逻辑", "count": 1,
                 "handles": ["@示例-游资博主"],
                 "content": "短期涨幅较大，注意获利回吐与指引扰动。",
                 "sources": [_src("微博", "示例-游资博主")]},
            ],
        },
        {
            "code": "688256", "name": "寒武纪", "group": "多空对垒",
            "tag": "分歧", "stance": "分歧", "kol_count": 6, "post_count": 11,
            "summary": "国产算力稀缺标的，看多押注份额扩张，看空担忧估值透支与商业化兑现。",
            "view_blocks": [
                {"kind": "多空综合分析", "count": 3,
                 "handles": ["@示例-宏观博主", "@示例-游资博主"],
                 "content": "多空分歧明显：政策订单驱动 vs 估值显著透支当期业绩。",
                 "sources": [_src("雪球", "示例-宏观博主"), _src("微博", "示例-游资博主")]},
                {"kind": "买入逻辑", "count": 2,
                 "handles": ["@示例-宏观博主"],
                 "content": "国产替代核心标的，政策与订单双驱动，稀缺性溢价。",
                 "sources": [_src("雪球", "示例-宏观博主")]},
                {"kind": "卖出 / 风险逻辑", "count": 1,
                 "handles": ["@示例-游资博主"],
                 "content": "估值已透支，需警惕情绪退潮与解禁压力。",
                 "sources": [_src("微博", "示例-游资博主")]},
            ],
        },
        {
            "code": "300750", "name": "宁德时代", "group": "多头共识",
            "tag": "热议", "stance": "看多", "kol_count": 5, "post_count": 7,
            "summary": "储能需求与海外份额是主要看点，估值处于历史中枢偏下。",
            "view_blocks": [
                {"kind": "买入逻辑", "count": 3,
                 "handles": ["@示例-宏观博主", "@示例-产业链博主"],
                 "content": "储能放量 + 海外扩张，盈利质量稳健，估值有修复空间。",
                 "sources": [_src("雪球", "示例-宏观博主"), _src("公众号", "示例-产业链博主")]},
                {"kind": "数据 / 客观观察", "count": 1,
                 "handles": ["@示例-产业链博主"],
                 "content": "竞争格局趋稳，龙头议价能力仍强。",
                 "sources": [_src("公众号", "示例-产业链博主")]},
            ],
        },
        {
            "code": "002253", "name": "川大智胜", "group": "多空对垒",
            "tag": "短线", "stance": "看多", "kol_count": 4, "post_count": 6,
            "summary": "短线情绪标的，今日竞价高开活跃，游资关注度高，分歧在持续性。",
            "view_blocks": [
                {"kind": "多空综合分析", "count": 2,
                 "handles": ["@示例-游资博主", "@示例-宏观博主"],
                 "content": "游资看接力弹性，稳健派认为纯情绪驱动、缺基本面支撑。",
                 "sources": [_src("微博", "示例-游资博主"), _src("雪球", "示例-宏观博主")]},
                {"kind": "卖出 / 风险逻辑", "count": 1,
                 "handles": ["@示例-宏观博主"],
                 "content": "纯情绪驱动，仓位需控制，注意退潮。",
                 "sources": [_src("雪球", "示例-宏观博主")]},
            ],
        },
    ],
    # ④ 其他热议（宏观 / 赛道级）
    "other_topics": [
        {"title": "AI 算力 capex 周期是否见顶的争论", "tag": "赛道",
         "content": "围绕北美云厂 capex 指引展开多空讨论，多数仍偏乐观。",
         "sources": [_src("雪球", "示例-宏观博主")]},
        {"title": "中东局势对油价与顺周期的传导", "tag": "宏观",
         "content": "关注供给扰动对有色/油气链条的短期催化。",
         "sources": [_src("公众号", "示例-产业链博主")]},
        {"title": "情绪周期位置研判：退潮还是修复", "tag": "情绪",
         "content": "连板高度下降，倾向退潮初期，建议降低仓位等修复。",
         "sources": [_src("微博", "示例-游资博主")]},
    ],
}


def get_digest(date: str = "") -> Dict:
    """当日 KOL 日报。date 预留（真实接入后按日期取库）。TODO: 改为真实采集库读取。"""
    return _MOCK_DIGEST
