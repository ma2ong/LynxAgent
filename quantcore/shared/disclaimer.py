"""统一免责声明：所有 AI 生成内容由代码层拼接，不依赖模型自觉。"""

AI_DISCLAIMER = (
    "本内容由 AI 基于公开数据自动生成，仅供研究参考，"
    "不构成投资建议。市场有风险，决策需独立。"
)


def attach_disclaimer(payload: dict) -> dict:
    """给 AI 输出 dict 附加免责字段（幂等）。"""
    if isinstance(payload, dict):
        payload.setdefault("disclaimer", AI_DISCLAIMER)
    return payload
