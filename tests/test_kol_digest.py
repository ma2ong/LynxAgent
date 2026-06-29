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


def test_discover_groups_authors_and_writes_file(monkeypatch, tmp_path):
    def fake_oj(args):
        if args[:2] == ["twitter", "search"]:
            return [{"author": "tw_kol", "text": "财经观点一二三四五", "url": "http://x/1", "likes": 7}]
        if args[:2] == ["weibo", "search"]:
            return [{"author": "wb_kol", "title": "微博财经一二三四", "url": "http://wb/1"}]
        return []
    monkeypatch.setattr(bkd, "_opencli_json", fake_oj)
    monkeypatch.setattr(bkd, "SEARCH_TERMS", ["t1", "t2"])
    monkeypatch.setattr(bkd, "FINANCE_KEYWORDS", ["t1"])
    monkeypatch.setattr(bkd, "CAND_PATH", tmp_path / "kol_candidates.json")
    cands = bkd.discover()
    by_author = {c["author"]: c for c in cands}
    assert by_author["tw_kol"]["platform"] == "X"
    assert by_author["tw_kol"]["count"] == 2      # 两个关键词各命中一次
    assert by_author["tw_kol"]["likes"] == 14
    assert by_author["wb_kol"]["count"] == 1
    assert (tmp_path / "kol_candidates.json").exists()


def test_collect_merges_three_sources_deduped(monkeypatch):
    def fake_oj(args):
        if args[:2] == ["twitter", "search"]:
            return [{"author": "x1", "text": "X 财经观点一二三四", "url": "http://x/1", "likes": 10}]
        if args[:2] == ["xueqiu", "hot"]:
            return [{"author": "xq1", "text": "雪球热门观点一二三", "url": "http://xq/1", "likes": 5}]
        if args[:2] == ["weibo", "search"]:
            return [{"author": "wb1", "title": "微博财经标题一二三", "url": "http://wb/1"}]
        return []  # feed / tweets / user-posts 空（无名单）
    monkeypatch.setattr(bkd, "_opencli_json", fake_oj)
    monkeypatch.setattr(bkd, "X_KOLS", [])
    monkeypatch.setattr(bkd, "WEIBO_KOLS", [])
    out = bkd.collect()
    assert {it["platform"] for it in out} == {"X", "雪球", "微博"}
    assert len(out) == 3   # 多关键词重复抓同一 url 被去重


def test_opencli_json_parses_subprocess_output(monkeypatch):
    def fake_run(cmd, stdout, **kw):
        stdout.write('[{"author":"a","text":"hello world test","url":"u","likes":3}]')
        return None
    monkeypatch.setattr(bkd.subprocess, "run", fake_run)
    out = bkd._opencli_json(["twitter", "search", "x"])
    assert out and out[0]["author"] == "a"


def test_opencli_json_returns_empty_on_bad_json(monkeypatch):
    def fake_run(cmd, stdout, **kw):
        stdout.write("not json")
        return None
    monkeypatch.setattr(bkd.subprocess, "run", fake_run)
    assert bkd._opencli_json(["weibo", "search", "x"]) == []


def test_assemble_matches_get_digest_contract():
    items = [
        {"platform": "X", "author": "x1", "url": "http://x/1", "text": "看多光模块一二三"},
        {"platform": "雪球", "author": "xq1", "url": "http://xq/1", "text": "估值偏高一二三"},
    ]
    agg = {
        "stocks": [{
            "name": "中际旭创", "group": "多头共识", "tag": "热议", "stance": "看多",
            "summary": "需求未见顶",
            "blocks": [
                {"kind": "买入逻辑", "content": "1.6T 放量", "tweets": [0]},
                {"kind": "卖出 / 风险逻辑", "content": "估值高", "tweets": [1]},
            ],
        }],
        "other_topics": [{"title": "算力 capex", "tag": "赛道", "content": "偏乐观", "tweets": [0]}],
    }

    def fake_resolve(names):
        return [{"symbol": "300308", "name": "中际旭创"}] if names and names[0] == "中际旭创" else []

    d = bkd._assemble(agg, items, fake_resolve)
    assert d["is_mock"] is False
    assert {"stats", "hottest", "attention_rank", "stocks", "other_topics", "sources_platform"} <= set(d)
    s = d["stocks"][0]
    assert s["code"] == "300308" and s["name"] == "中际旭创"
    plats = {src["platform"] for b in s["view_blocks"] for src in b["sources"]}
    assert plats == {"X", "雪球"}                 # 两个平台来源都在
    assert d["sources_platform"] == ["X", "雪球"]  # 并集（排序后）


def test_assemble_returns_none_when_no_resolvable_stock():
    items = [{"platform": "X", "author": "x1", "url": "u", "text": "随便一二三四五"}]
    agg = {"stocks": [{"name": "查无此股", "blocks": [{"kind": "x", "content": "y", "tweets": [0]}]}],
           "other_topics": []}
    assert bkd._assemble(agg, items, lambda names: []) is None


def test_sources_carries_real_platform_not_hardcoded_x():
    items = [
        {"platform": "X", "author": "x1", "url": "http://x/1"},
        {"platform": "雪球", "author": "xq1", "url": "http://xq/1"},
        {"platform": "微博", "author": "wb1", "url": ""},   # 无 url → is_placeholder
    ]
    srcs = bkd._sources([0, 1, 2], items)
    plats = [s["platform"] for s in srcs]
    assert plats == ["X", "雪球", "微博"]            # 不再全部 "X"
    assert srcs[2]["is_placeholder"] is True        # 无 url 标占位
    assert srcs[0]["is_placeholder"] is False


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
