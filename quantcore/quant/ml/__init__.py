"""qlib 风格的机器学习因子链：因子矩阵 → LightGBM → 打分排序 → 滚动再训练。

跑在现有本地数据层（local_store）之上，不引入 qlib 框架本体。
"""
