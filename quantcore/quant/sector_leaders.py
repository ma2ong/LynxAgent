"""个股深研「按赛道浏览龙头股」的策划配置（A 股版，对标 themarketbrew /stock）。

人工策划而非自动推导：龙头股名单稳定、可解释，避免每日榜单漂移。
名称仅作行情失败时的兜底，实时行情由 endpoint 用腾讯实时报价覆盖。
需要增删赛道/成分股时直接改本表即可。
"""
from __future__ import annotations

from typing import Dict, List

# 每个赛道：key 唯一标识，name 中文名，en 英文副名，subtitle 一句话定位，
# leaders 为 [(代码, 名称)]。代码统一 6 位。
SECTOR_LEADERS: List[Dict[str, object]] = [
    {
        "key": "ai_compute", "name": "AI 算力 / 光模块", "en": "AI Infrastructure",
        "subtitle": "算力军火商，跟踪 AI capex 周期景气度",
        "leaders": [
            ("300308", "中际旭创"), ("300502", "新易盛"), ("300394", "天孚通信"),
            ("601138", "工业富联"), ("002463", "沪电股份"), ("688256", "寒武纪"),
        ],
    },
    {
        "key": "semiconductor", "name": "半导体", "en": "Semiconductor",
        "subtitle": "国产替代主线，设备/制造/设计全链",
        "leaders": [
            ("688981", "中芯国际"), ("002371", "北方华创"), ("688012", "中微公司"),
            ("603501", "韦尔股份"), ("603986", "兆易创新"), ("688041", "海光信息"),
        ],
    },
    {
        "key": "consumer_electronics", "name": "消费电子 / 果链", "en": "Consumer Electronics",
        "subtitle": "端侧 AI + 新品周期驱动",
        "leaders": [
            ("002475", "立讯精密"), ("002241", "歌尔股份"), ("300433", "蓝思科技"),
            ("002938", "鹏鼎控股"), ("002405", "四维图新"),
        ],
    },
    {
        "key": "new_energy", "name": "新能源车 / 锂电", "en": "New Energy",
        "subtitle": "全球电动化 + 储能需求",
        "leaders": [
            ("300750", "宁德时代"), ("002594", "比亚迪"), ("300014", "亿纬锂能"),
            ("300274", "阳光电源"), ("002460", "赣锋锂业"),
        ],
    },
    {
        "key": "innovation_pharma", "name": "创新药 / 医药", "en": "Healthcare",
        "subtitle": "创新管线兑现 + 出海逻辑",
        "leaders": [
            ("600276", "恒瑞医药"), ("603259", "药明康德"), ("688235", "百济神州"),
            ("300760", "迈瑞医疗"), ("002821", "凯莱英"),
        ],
    },
    {
        "key": "robotics", "name": "机器人 / 自动化", "en": "Robotics",
        "subtitle": "人形机器人零部件 + 工业自动化",
        "leaders": [
            ("300124", "汇川技术"), ("688017", "绿的谐波"), ("002050", "三花智控"),
            ("002747", "埃斯顿"), ("603728", "鸣志电器"),
        ],
    },
    {
        "key": "liquor_consumer", "name": "白酒 / 消费", "en": "Consumer Staples",
        "subtitle": "防御性配置，盈利稳定",
        "leaders": [
            ("600519", "贵州茅台"), ("000858", "五粮液"), ("000568", "泸州老窖"),
            ("600809", "山西汾酒"), ("603288", "海天味业"),
        ],
    },
    {
        "key": "defense", "name": "军工 / 低空经济", "en": "Defense & eVTOL",
        "subtitle": "国防订单 + 低空政策催化",
        "leaders": [
            ("600760", "中航沈飞"), ("600893", "航发动力"), ("600038", "中直股份"),
            ("000768", "中航西飞"), ("002013", "中航机载"),
        ],
    },
    {
        "key": "finance", "name": "金融 / 券商", "en": "Financials",
        "subtitle": "成交放大时弹性增强，市场晴雨表",
        "leaders": [
            ("600030", "中信证券"), ("300059", "东方财富"), ("600036", "招商银行"),
            ("601318", "中国平安"), ("601166", "兴业银行"),
        ],
    },
    {
        "key": "power_utility", "name": "电力 / 电网", "en": "Power & Grid",
        "subtitle": "数据中心能耗 + 电力设备配套",
        "leaders": [
            ("600900", "长江电力"), ("600406", "国电南瑞"), ("601985", "中国核电"),
            ("600886", "国投电力"), ("002028", "思源电气"),
        ],
    },
    {
        "key": "resources", "name": "资源 / 有色", "en": "Resources",
        "subtitle": "避险与通胀交易，外盘价格驱动",
        "leaders": [
            ("601899", "紫金矿业"), ("600111", "北方稀土"), ("603993", "洛阳钼业"),
            ("000831", "中国稀土"), ("600547", "山东黄金"),
        ],
    },
    {
        "key": "advanced_manufacturing", "name": "高端制造 / 工程机械", "en": "Industrials",
        "subtitle": "出海 + 周期复苏",
        "leaders": [
            ("600031", "三一重工"), ("000157", "中联重科"), ("601100", "恒立液压"),
            ("002008", "大族激光"), ("603501", "韦尔股份"),
        ],
    },
]


def all_leader_codes() -> List[str]:
    """所有赛道去重后的成分股代码，供批量取实时行情。"""
    seen: Dict[str, None] = {}
    for sector in SECTOR_LEADERS:
        for code, _name in sector["leaders"]:  # type: ignore[union-attr]
            seen.setdefault(str(code), None)
    return list(seen.keys())
