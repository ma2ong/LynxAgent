import json

from quantcore.quant import limit_up_taxonomy
from quantcore.quant.limit_up_taxonomy import limit_up_reason, resolve_limit_up_concept


def test_limit_up_taxonomy_maps_recent_hotspot_stocks():
    cases = {
        "002851": ("麦格米特", "电力设备"),
        "002815": ("崇达技术", "AI硬件"),
        "002897": ("意华股份", "AI硬件"),
        "601022": ("宁波远洋", "港口航运"),
        "002961": ("瑞达期货", "金融服务"),
        "002972": ("科安达", "轨交设备"),
        "002436": ("兴森科技", "AI硬件"),
        "000034": ("神州数码", "数据中心"),
        "002194": ("武汉凡谷", "光通信/CPO"),
        "000608": ("阳光股份", "地产链"),
        "000012": ("南  玻Ａ", "化工材料"),
    }

    for symbol, (name, expected) in cases.items():
        assert resolve_limit_up_concept(symbol, name, "", None) == expected


def test_limit_up_reason_uses_stock_specific_context():
    reason = limit_up_reason(
        name="麦格米特",
        symbol="002851",
        cause="电力设备",
        boards=1,
        is_one_price=False,
        is_big=True,
        is_20pct=False,
        amount_yi=29.64,
    )

    assert "成交额29.6亿" in reason
    assert "AI 服务器电源" in reason
    assert "电力设备链" in reason


def test_external_limit_up_taxonomy_overrides_builtin(tmp_path, monkeypatch):
    taxonomy_file = tmp_path / "limit_up_concepts.json"
    taxonomy_file.write_text(
        json.dumps(
            {
                "version": "test-v1",
                "by_code": {
                    "002851": {
                        "concept": "AI硬件",
                        "reason": "测试外置动因，优先覆盖内置分类。",
                    }
                },
                "keyword_rules": [
                    {"concept": "光通信/CPO", "keywords": ["测试光器件"]}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LYNX_LIMIT_UP_TAXONOMY_FILE", str(taxonomy_file))
    limit_up_taxonomy._EXTERNAL_TAXONOMY_CACHE.update({"path": "", "mtime_ns": -1, "data": {}})

    assert resolve_limit_up_concept("002851", "麦格米特", "", None) == "AI硬件"
    assert resolve_limit_up_concept("999999", "测试股", "测试光器件", None) == "光通信/CPO"

    reason = limit_up_reason(
        name="麦格米特",
        symbol="002851",
        cause="AI硬件",
        boards=1,
        is_one_price=False,
        is_big=False,
        is_20pct=False,
        amount_yi=0,
    )
    assert "测试外置动因" in reason
    assert "test-v1" in limit_up_taxonomy.limit_up_taxonomy_version()
