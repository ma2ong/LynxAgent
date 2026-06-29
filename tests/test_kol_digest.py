"""KOL 采集器离线单测：按路径加载 scripts/build_kol_digest.py，不联网、不调 LLM。"""
import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_kol_digest.py"


def _load_bkd():
    spec = importlib.util.spec_from_file_location("build_kol_digest", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bkd = _load_bkd()


def test_module_loads():
    assert hasattr(bkd, "collect")


def test_norm_weibo_search_uses_title_and_zero_likes():
    raw = {"title": "微博财经标题一二三四五", "author": "wb1", "url": "http://wb/1", "time": "x"}
    it = bkd._norm("微博", raw)
    assert it["platform"] == "微博"
    assert it["text"] == "微博财经标题一二三四五"
    assert it["likes"] == 0
    assert it["url"] == "http://wb/1"


def test_norm_twitter_carries_text_and_likes():
    raw = {"text": "X 财经观点一二三四", "author": "x1", "url": "http://x/1", "likes": "12"}
    it = bkd._norm("X", raw)
    assert it["platform"] == "X" and it["author"] == "x1"
    assert it["text"] == "X 财经观点一二三四" and it["likes"] == 12


def test_dedup_rank_cross_platform_kept_same_platform_deduped():
    items = [
        {"platform": "X", "author": "a", "text": "同链接不同平台一二三", "url": "u1", "likes": 1},
        {"platform": "微博", "author": "b", "text": "同链接不同平台一二三", "url": "u1", "likes": 9},
        {"platform": "X", "author": "a", "text": "同链接不同平台一二三", "url": "u1", "likes": 1},  # 与第1条重复
        {"platform": "X", "author": "c", "text": "短", "url": "u2", "likes": 99},  # 文本太短被丢
    ]
    out = bkd._dedup_rank(items, 10)
    assert len(out) == 2                      # 跨平台保留、同平台同链接去重、短文本丢弃
    assert out[0]["platform"] == "微博"        # likes 高在前
