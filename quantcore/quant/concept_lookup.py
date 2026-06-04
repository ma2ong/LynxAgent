"""按需从 AKShare 拉取概念板块成分，缓存到进程内存（日级刷新）。

用法：get_concept(stock_name) -> 概念标签 or None
"""
from __future__ import annotations

import logging
import threading
from datetime import date
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# 要抓取的概念板块关键词 → 展示标签（顺序即优先级，先命中者优先）
CONCEPT_TARGETS: list[tuple[str, str]] = [
    ("光模块", "光通信/CPO"),
    ("CPO", "光通信/CPO"),
    ("光通信", "光通信/CPO"),
    ("AI算力", "AI硬件"),
    ("人工智能", "AI硬件"),
    ("AIPC", "AI硬件"),
    ("机器人", "机器人"),
    ("工业母机", "机器人"),
    ("减速器", "机器人"),
    ("半导体", "国产芯片"),
    ("芯片", "国产芯片"),
    ("集成电路", "国产芯片"),
    ("煤炭", "煤炭"),
    ("焦煤", "煤炭"),
    ("电力", "电力"),
    ("储能", "电力"),
    ("核电", "电力"),
    ("新能源车", "新能源车"),
    ("充电桩", "新能源车"),
    ("动力电池", "新能源车"),
    ("白酒", "大消费"),
    ("消费电子", "大消费"),
    ("食品饮料", "大消费"),
    ("航天", "航天军工"),
    ("军工", "航天军工"),
    ("低空经济", "航天军工"),
    ("无人机", "航天军工"),
    ("创新药", "医药"),
    ("医疗器械", "医药"),
    ("CXO", "医药"),
    ("黄金", "有色金属"),
    ("铜", "有色金属"),
    ("锂", "有色金属"),
    ("重组", "公告重组"),
    ("并购", "公告重组"),
]

# 静态种子字典：上线时的兜底，网络不可用时也能分类热门股
_SEED: Dict[str, str] = {
    # 光通信/CPO
    "亨通光电": "光通信/CPO", "天洋新材": "光通信/CPO", "青山纸业": "光通信/CPO",
    "中广核技": "光通信/CPO", "晶赛科技": "光通信/CPO", "大唐电信": "光通信/CPO",
    "旭光电子": "光通信/CPO", "三安光电": "光通信/CPO", "新易盛": "光通信/CPO",
    "中际旭创": "光通信/CPO", "天孚通信": "光通信/CPO", "华工科技": "光通信/CPO",
    # AI硬件
    "惠丰钻石": "AI硬件", "红星发展": "AI硬件", "东材科技": "AI硬件",
    "国机精工": "AI硬件", "王子新材": "AI硬件", "迅捷兴": "AI硬件",
    "一博科技": "AI硬件", "恒星科技": "AI硬件", "沪电股份": "AI硬件",
    "胜宏科技": "AI硬件", "深南电路": "AI硬件",
    # 国产芯片
    "通富微电": "国产芯片", "实益达": "国产芯片", "华特气体": "国产芯片",
    "三孚股份": "国产芯片", "三祥新材": "国产芯片", "康强电子": "国产芯片",
    "中旗新材": "国产芯片", "华源控股": "国产芯片", "肯特催化": "国产芯片",
    "中微公司": "国产芯片", "北方华创": "国产芯片", "韦尔股份": "国产芯片",
    # 机器人
    "模塑科技": "机器人", "北投科技": "机器人", "美湖股份": "机器人",
    "移远通信": "机器人", "长华集团": "机器人", "祥鑫科技": "机器人",
    "德昌股份": "机器人", "欧克科技": "机器人", "神驰机电": "机器人",
    "泰禾智能": "机器人", "中重科技": "机器人", "一彬科技": "机器人",
    "协昌科技": "机器人", "绿的谐波": "机器人", "汇川技术": "机器人",
    # 煤炭
    "郑州煤电": "煤炭", "大有能源": "煤炭", "兖矿能源": "煤炭",
    "中国神华": "煤炭", "陕西煤业": "煤炭", "平煤股份": "煤炭",
    # 电力
    "华电辽能": "电力", "豫能控股": "电力", "京能电力": "电力",
    "广西能源": "电力", "恒盛能源": "电力", "西昌电力": "电力",
    "新中港": "电力", "瑞贝卡": "电力", "上海石化": "电力",
    "万通发展": "电力", "芯能科技": "电力",
    # 大消费
    "元祖股份": "大消费", "利仁科技": "大消费", "安奈儿": "大消费",
    "上海凤凰": "大消费", "中国神华": "大消费",
    # 航天军工
    "久之洋": "航天军工", "海特高新": "航天军工", "中无人机": "航天军工",
    # 公告重组
    "节能铁汉": "公告重组", "金海高科": "公告重组", "联检科技": "公告重组", "蓝科高新": "公告重组",
}

_cache: Dict[str, str] = dict(_SEED)  # 初始化时就用种子填充
_cache_date: Optional[date] = None
_lock = threading.Lock()


def _build_cache() -> Dict[str, str]:
    """尝试用 AKShare 概念板块数据扩充缓存。失败时返回种子字典。"""
    try:
        import pandas as pd
        pd.options.future.infer_string = False
        import akshare as ak
    except ImportError:
        return dict(_SEED)

    # 获取全部概念板块列表（列名乱码，用位置取）
    try:
        board_df = ak.stock_board_concept_name_em()
        # 第1列（index=1）是板块名称
        board_names: list[str] = board_df.iloc[:, 1].astype(str).tolist()
    except Exception:
        return dict(_SEED)

    mapping: Dict[str, str] = dict(_SEED)  # 以种子为基础
    used_labels: set[str] = set()

    for keyword, label in CONCEPT_TARGETS:
        if label in used_labels:
            continue  # 同一 label 只抓一次（先写入的优先级高）
        matched = next((n for n in board_names if keyword in n), None)
        if not matched:
            continue
        try:
            cons_df = ak.stock_board_concept_cons_em(symbol=matched)
            # 名称列通常是第2列（index=1）
            name_field = cons_df.columns[1] if len(cons_df.columns) > 1 else cons_df.columns[0]
            for sname in cons_df[name_field].dropna().astype(str):
                sname = sname.strip()
                if sname and sname not in mapping:  # 种子优先级更高，不覆盖
                    mapping[sname] = label
            used_labels.add(label)
        except Exception as exc:
            logger.debug("concept_lookup: skip %s — %s", matched, exc)

    logger.info("concept_lookup: cache built with %d entries (seed=%d)", len(mapping), len(_SEED))
    return mapping


_enriching = False  # 是否正在后台异步更新


def _enrich_async() -> None:
    """后台线程：用 AKShare 扩充缓存，完成后合并入 _cache。"""
    global _cache, _cache_date, _enriching
    try:
        new_cache = _build_cache()
        with _lock:
            _cache = new_cache
            _cache_date = date.today()
    except Exception as exc:
        logger.warning("concept_lookup: async enrich failed — %s", exc)
    finally:
        _enriching = False


def get_concept(name: str) -> Optional[str]:
    """返回股票名称对应的概念标签，无映射时返回 None。

    首次调用立即用种子字典（<1ms），同时在后台异步抓 AKShare 扩充数据；
    次日第一次调用再触发一次后台更新。
    """
    global _cache_date, _enriching
    today = date.today()
    # 触发后台异步更新（每日一次，不阻塞调用方）
    with _lock:
        if _cache_date != today and not _enriching:
            _enriching = True
            t = threading.Thread(target=_enrich_async, daemon=True)
            t.start()
    return _cache.get(str(name).strip())
