"""板块轮动：行业相对强度排名与领先/落后标定。"""
from quantcore.quant.sector_rotation import rank_sectors


def _rows(spec):
    """spec: {symbol: (ret_5, ret_20, ret_60)} → chunk 输出形状（代码补足 6 位与真实一致）。"""
    return [{"symbol": s.zfill(6), "ret_5": r5, "ret_20": r20, "ret_60": r60, "amount": 1e8}
            for s, (r5, r20, r60) in spec.items()]


def _imap(spec):
    return {s.zfill(6): ind for s, ind in spec.items()}


def test_leading_sector_ranks_first():
    """强势行业(高收益) RS 为正、排名靠前；弱势行业 RS 为负、垫底。"""
    imap = _imap({"001": "半导体", "002": "半导体", "003": "半导体",
                  "011": "银行", "012": "银行", "013": "银行",
                  "021": "煤炭", "022": "煤炭", "023": "煤炭"})
    rows = _rows({
        "001": (5, 20, 40), "002": (4, 18, 38), "003": (6, 22, 42),   # 半导体强
        "011": (0, 1, 2), "012": (0.5, 0, 1), "013": (0, 2, 3),        # 银行中性
        "021": (-3, -10, -20), "022": (-2, -8, -18), "023": (-4, -12, -22),  # 煤炭弱
    })
    res = rank_sectors(rows, imap)
    assert res["sectors"][0]["name"] == "半导体"
    assert res["sectors"][-1]["name"] == "煤炭"
    assert res["sectors"][0]["rs_12w"] > 0     # 强于大盘
    assert res["sectors"][-1]["rs_12w"] < 0    # 弱于大盘
    assert "半导体" in res["leaders"]
    assert "煤炭" in res["laggards"]


def test_min_members_filter():
    """成分股不足 min_members 的行业不参与排名（样本太小不可信）。"""
    imap = _imap({"001": "半导体", "002": "半导体", "003": "半导体", "099": "冷门"})
    rows = _rows({"001": (5, 20, 40), "002": (4, 18, 38), "003": (6, 22, 42), "099": (99, 99, 99)})
    res = rank_sectors(rows, imap)
    names = [s["name"] for s in res["sectors"]]
    assert "冷门" not in names
    assert "半导体" in names


def test_rs_is_relative_to_market():
    """RS = 行业中位 − 全市场中位；全员齐涨时强弱看相对而非绝对。"""
    imap = _imap({f"{i:03d}": ("A" if i < 3 else "B") for i in range(6)})
    rows = _rows({
        "000": (0, 0, 30), "001": (0, 0, 32), "002": (0, 0, 31),   # A: 约 +31
        "003": (0, 0, 10), "004": (0, 0, 11), "005": (0, 0, 9),    # B: 约 +10
    })
    res = rank_sectors(rows, imap)
    a = next(s for s in res["sectors"] if s["name"] == "A")
    b = next(s for s in res["sectors"] if s["name"] == "B")
    assert a["rs_12w"] > 0 > b["rs_12w"]  # A 强于大盘中位，B 弱于


def test_empty_safe():
    res = rank_sectors([], {})
    assert res["sectors"] == [] and res["leaders"] == [] and res["laggards"] == []
