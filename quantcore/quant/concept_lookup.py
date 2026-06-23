"""涨停概念归因：动态热门概念板块映射（按日落盘缓存）。

设计目标——准确且可靠：
- 准确：每只股票归到它当日所属的「最热概念板块」，资金主线随盘面自动更新，
  不再依赖手工维护的静态代码字典。
- 可靠：板块→成分股映射按交易日落盘到 runtime/concept_map_YYYYMMDD.json，
  每天后台只构建一次、跨进程/重启复用；东财接口限流/失败时回退到静态种子字典，
  调用方永远 <1ms 拿到结果，不阻塞。

对外：
  get_concept(name) -> Optional[str]                  # 概念桶标签
  get_hot_concept(symbol, name) -> Optional[(桶, 真实板块名)]  # 涨停归因 + 动因文案用
  name_concept_map() -> {股票名: 概念桶}              # 受益股反查（serenity_resolve 用）

向后兼容：模块级 `_cache`（{股票名: 概念桶}）保持可用并随热门板块映射实时刷新，
serenity_resolve 仍可 getattr(concept_lookup, "_cache") 读到（且自动用上实时数据）。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date
from typing import Dict, Optional, Tuple

from .limit_up_taxonomy import CONCEPT_ORDER, KEYWORD_RULES

logger = logging.getLogger(__name__)

_MAP_DIR = os.environ.get("QUANT_DATA_DIR", "runtime")
# 取当日涨幅前 N 的「可映射主题板块」抓成分股，聚焦真正的资金主线、控制请求量。
_HOT_BOARD_LIMIT = 45
# 元/梯队/指数类板块：不是行业主题，即使含主题词也排除。
_EXCLUDE_BOARD_KEYS = (
    "涨停", "连板", "昨日", "融资", "融券", "转融", "股通", "沪深",
    "成份", "成分", "权重", "核心资产", "破净", "次新", "注册制",
    "高送转", "百元", "ST", "MSCI", "标普", "富时", "转债", "回购",
)


def _board_to_bucket(board_name: str) -> Optional[str]:
    """概念板块名 → CONCEPT_ORDER 桶；无法归类返回 None（自动滤掉梯队/指数板块）。"""
    text = str(board_name or "")
    if any(bad in text for bad in _EXCLUDE_BOARD_KEYS):
        return None
    for label, keywords in KEYWORD_RULES:
        if any(kw and kw in text for kw in keywords):
            return label
    return None


# 静态种子字典：上线/网络不可用时的兜底，确保高频热门股也能分类，不落入“其他”。
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
    "江丰电子": "国产芯片", "奥士康": "国产芯片", "雅克科技": "国产芯片",
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
    "火星人": "大消费", "海信家电": "大消费", "海尔智家": "大消费", "美的集团": "大消费",
    # 有色金属
    "章源钨业": "有色金属", "翔鹭钨业": "有色金属", "海亮股份": "有色金属",
    "云南锗业": "有色金属", "国城矿业": "有色金属", "西部矿业": "有色金属",
    "中国铝业": "有色金属", "紫金矿业": "有色金属", "赤峰黄金": "有色金属",
    # 医药
    "利德曼": "医药", "迈瑞医疗": "医药", "药明康德": "医药",
    "康龙化成": "医药", "泰格医药": "医药",
    # 新能源车
    "多氟多": "新能源车", "宁德时代": "新能源车", "比亚迪": "新能源车",
    # 航天军工
    "久之洋": "航天军工", "海特高新": "航天军工", "中无人机": "航天军工",
    "中信海直": "航天军工", "中航西飞": "航天军工", "万丰奥威": "航天军工",
    "航天发展": "航天军工", "中直股份": "航天军工", "洪都航空": "航天军工",
    "中航机电": "航天军工", "中航沈飞": "航天军工", "航发控制": "航天军工",
    "航发动力": "航天军工", "中航光电": "航天军工", "成飞集成": "航天军工",
    "中航电子": "航天军工", "中航重机": "航天军工", "中国航发": "航天军工",
    "中国航空": "航天军工", "航天电器": "航天军工", "航天彩虹": "航天军工",
    "中天火箭": "航天军工", "天奥电子": "航天军工", "鸿远电子": "国产芯片",
    "北方导航": "航天军工", "四创电子": "航天军工", "雷电微力": "航天军工",
    # 有色金属 additions
    "安泰科技": "有色金属", "宝钛股份": "有色金属", "西部材料": "有色金属",
    "中国钼业": "有色金属", "洛阳钼业": "有色金属",
    "焦作万方": "有色金属", "中色股份": "有色金属", "锌业股份": "有色金属",
    "南矿集团": "有色金属", "德冠新材": "有色金属",  # 铝箔
    "铜陵有色": "有色金属", "云南铜业": "有色金属", "江西铜业": "有色金属",
    "北方铜业": "有色金属", "云南钼业": "有色金属", "招金矿业": "有色金属",
    "中铝股份": "有色金属", "神火股份": "有色金属",
    # 新能源车 additions
    "雄韬股份": "新能源车",  # 锂电/燃料电池
    # 电力 additions
    "泰永长征": "电力", "华明装备": "电力", "电投产融": "电力",
    # 大消费 additions
    "潮宏基": "大消费", "名雕股份": "大消费",
    # 公告重组
    "节能铁汉": "公告重组", "金海高科": "公告重组", "联检科技": "公告重组", "蓝科高新": "公告重组",
}

# 运行态映射：name/code → (桶, 真实板块名)，由当日热门概念板块构建。
_by_name: Dict[str, Tuple[str, str]] = {}
_by_code: Dict[str, Tuple[str, str]] = {}
# 向后兼容：{股票名: 概念桶}，serenity_resolve 仍读它；种子初始化，热门板块构建后实时刷新。
_cache: Dict[str, str] = dict(_SEED)
_map_date: Optional[date] = None
_building = False
_lock = threading.Lock()


def _map_path(d: str) -> str:
    return os.path.join(_MAP_DIR, f"concept_map_{d}.json")


def _retry(fn, attempts: int = 3, base_delay: float = 1.5):
    for i in range(attempts):
        try:
            return fn()
        except Exception:
            if i < attempts - 1:
                time.sleep(base_delay * (i + 1))
    return None


def _load_disk(d: str) -> Optional[dict]:
    try:
        with open(_map_path(d), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_disk(d: str, payload: dict) -> None:
    try:
        os.makedirs(_MAP_DIR, exist_ok=True)
        with open(_map_path(d), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception as exc:
        logger.debug("concept_lookup: save disk failed — %s", exc)


def _apply_payload(payload: dict) -> None:
    """落地热门板块映射，并同步刷新向后兼容的 _cache（种子 + 实时）。"""
    global _by_name, _by_code, _cache
    by_name = {k: tuple(v) for k, v in (payload.get("by_name") or {}).items()}
    by_code = {k: tuple(v) for k, v in (payload.get("by_code") or {}).items()}
    cache = dict(_SEED)
    for nm, hit in by_name.items():
        if hit:
            cache[nm] = hit[0]
    with _lock:
        _by_name = by_name
        _by_code = by_code
        _cache = cache


def _build_live_payload() -> Optional[dict]:
    """用东财概念板块当日涨幅排行 + 成分股，构建「最热板块」归因表。"""
    try:
        import pandas as pd
        pd.options.future.infer_string = False
        import akshare as ak
    except ImportError:
        return None

    nm = _retry(lambda: ak.stock_board_concept_name_em())
    if nm is None or getattr(nm, "empty", True):
        return None
    cols = list(nm.columns)
    name_col = next((c for c in cols if "名称" in str(c)), None)
    pct_col = next((c for c in cols if "涨跌幅" in str(c)), None)
    if not name_col:
        return None

    boards = []
    for _, row in nm.iterrows():
        bname = str(row.get(name_col) or "").strip()
        bucket = _board_to_bucket(bname)
        if not bucket:
            continue
        try:
            pct = float(row.get(pct_col)) if pct_col else 0.0
        except (TypeError, ValueError):
            pct = 0.0
        boards.append((bname, pct, bucket))
    if not boards:
        return None
    # 从最热板块开始，先写入者（更热）优先 → 每只股票归到它当日最强的概念主线。
    boards.sort(key=lambda x: x[1], reverse=True)

    by_name: Dict[str, list] = {}
    by_code: Dict[str, list] = {}
    fetched = 0
    for bname, _pct, bucket in boards[:_HOT_BOARD_LIMIT]:
        cons = _retry(lambda b=bname: ak.stock_board_concept_cons_em(symbol=b), attempts=2)
        if cons is None or getattr(cons, "empty", True):
            continue
        ccols = list(cons.columns)
        ccode = next((c for c in ccols if "代码" in str(c)), None)
        cname = next((c for c in ccols if "名称" in str(c)), None)
        for _, r in cons.iterrows():
            sname = str(r.get(cname) or "").strip() if cname else ""
            code = str(r.get(ccode) or "").strip().zfill(6) if ccode else ""
            if code and code.isdigit() and code not in by_code:
                by_code[code] = [bucket, bname]
            if sname and sname not in by_name:
                by_name[sname] = [bucket, bname]
        fetched += 1
    if fetched == 0:
        return None
    logger.info("concept_lookup: live map built — %d boards, %d codes", fetched, len(by_code))
    return {"by_code": by_code, "by_name": by_name, "boards": fetched}


def _ensure_today(blocking: bool = False) -> None:
    """确保当日映射就绪：优先磁盘缓存，否则后台构建（种子兜底，不阻塞调用方）。"""
    global _map_date, _building
    today = date.today()
    if _map_date == today and (_by_name or _by_code):
        return
    # 构建在途时立即早返回，避免被逐行调用（如 limit_up 全 df apply 数万次）反复做磁盘 IO。
    if _building and not blocking:
        return
    disk = _load_disk(today.strftime("%Y-%m-%d"))
    if disk:
        _apply_payload(disk)
        _map_date = today
        return

    def _run():
        global _map_date, _building
        try:
            payload = _build_live_payload()
            if payload:
                _save_disk(today.strftime("%Y-%m-%d"), payload)
                _apply_payload(payload)
                _map_date = today
        except Exception as exc:
            logger.warning("concept_lookup: build failed — %s", exc)
        finally:
            _building = False

    if blocking:
        _building = True
        _run()
        return
    with _lock:
        if _building:
            return
        _building = True
    threading.Thread(target=_run, daemon=True).start()


def get_hot_concept(symbol: str, name: str) -> Optional[Tuple[str, str]]:
    """返回 (概念桶, 真实热门板块名)；无动态映射时回退种子字典 → (桶, '')。"""
    _ensure_today()
    code = str(symbol or "").strip().zfill(6)
    nm = str(name or "").strip()
    with _lock:
        hit = _by_code.get(code) or _by_name.get(nm)
    if hit:
        return hit[0], hit[1]
    seeded = _SEED.get(nm)
    if seeded:
        return seeded, ""
    return None


def get_concept(name: str) -> Optional[str]:
    """返回股票名称对应的概念桶标签，无映射时返回 None。

    优先当日动态热门板块映射，其次静态种子；后台异步构建，绝不阻塞调用方。
    """
    _ensure_today()
    nm = str(name or "").strip()
    with _lock:
        hit = _by_name.get(nm)
    if hit:
        return hit[0]
    return _SEED.get(nm)


def name_concept_map() -> Dict[str, str]:
    """返回 {股票名: 概念桶}，合并当日动态映射与静态种子，供受益股反查（serenity_resolve 用）。"""
    _ensure_today()
    out: Dict[str, str] = dict(_SEED)
    with _lock:
        for nm, hit in _by_name.items():
            if hit:
                out[nm] = hit[0]
    return out
