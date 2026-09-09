"""集合竞价异动观察名单：展示上限，以及「不再声称能排强弱」这条降级本身。

只测「给多少只」和「没有推荐包装漏回来」，不测选股逻辑好不好——后者由留痕、
复盘页和 experiments/ 回答，2026-09-09 那轮的结论是它没有 alpha。
"""
from quantcore.quant.call_auction import compute_call_auction

INDUSTRY = "半导体"


def _snapshot(n: int, opens: list[float] | None = None) -> dict:
    """造 n 只都够格进买入候选的票：高开、放量、成交额充足。

    opens 给定时按它设置各自的开盘涨幅。
    """
    snap = {}
    for i in range(n):
        code = f"{300000 + i:06d}"
        snap[code] = {
            "code": code,
            "name": f"票{i}",
            "prev_close": 10.0,
            # 开盘涨幅拉开一点点差距，保证排序稳定、不会并列到影响截断
            "open": 10.0 * (1 + ((opens[i] if opens else 3.0 + i * 0.05)) / 100),
            "price": 10.4,
            "amount": 5e8,
            "volume_ratio": 5.0,
            "turnover_rate": 8.0,
            "total_mv": 8e9,
        }
    return snap


def _run(n: int, opens: list[float] | None = None, **kw) -> dict:
    return compute_call_auction(
        _snapshot(n, opens), [],
        industry_map={f"{300000 + i:06d}": INDUSTRY for i in range(n)},
        hot_industries={INDUSTRY: 5.0},
        record=False,
        **kw,
    )


def test_display_is_capped_at_five():
    """够格的再多，页面也最多给 5 只。

    竞价窗口只有几分钟可看，一口气列十几只等于没列。上限是产品决定
    （2026-09-01 Allen 定），不是评分算法的结果。
    """
    out = _run(30)
    assert out["available"] is True
    assert len(out["watch_candidates"]) <= 5
    assert out["display_limit"] == 5


def test_short_list_is_not_padded():
    """够格的不足 5 只时按实际给，不拿弱票凑数。"""
    out = _run(2)
    assert len(out["watch_candidates"]) <= 2


def test_hidden_count_covers_what_the_cap_held_back():
    """被上限挡下的必须计入未展示数，否则用户会以为今天只有这么点货。"""
    out = _run(30)
    assert out["hidden_candidates"] >= 1


def test_record_sample_is_not_truncated_by_the_display_cap():
    """留痕仍按 buy_limit 记满：把展示上限套到留痕上等于砍掉三分之二的复盘样本。

    这条规则已被判定没有 alpha，但样本还要继续积累——不继续观测就无法回答
    「换了市场环境它会不会变」，那等于用一次结论把这个问题永久关死。
    """
    out = _run(30, buy_limit=15)
    assert out["hidden_candidates"] + len(out["watch_candidates"]) >= 15


def test_no_recommendation_packaging_leaks_back():
    """降级守卫：名单里不许再出现名次、推荐档位、综合强度分。

    2026-09-09 下线的就是这三样。它们声称的是「这几只里哪只更强」，而 46 天 /
    601 条留痕实测名次与当日收益的 Spearman ≈ 0，匹配对照增量 −0.81pp。
    哪天有人凭手感把它们加回来，这条测试要先拦一次。
    """
    out = _run(30)
    assert out["watch_candidates"]
    for c in out["watch_candidates"]:
        assert "rank" not in c
        assert "tier" not in c
        assert "strength" not in c
    for gone in ("tier_note", "relative_only", "strong_tier_count", "buy_candidates"):
        assert gone not in out


def test_note_states_the_measured_result_not_a_promise():
    """卡片文案必须把实测结论摆在前面，不能只说「仅供参考」。"""
    out = _run(30)
    assert "不是买入清单" in out["note"] or "不要当买入清单" in out["note"]
    assert out["hit_stats"]["sessions"] == 46
    assert out["hit_stats"]["matched_control_pp"] < 0


def test_recorded_codes_are_exposed_for_pattern_backfill():
    """留痕的代码要回传，否则路由层没法把盘口形态补进 picks_history。

    形态要额外拉盘前分时才算得出，留痕发生在那之前；不回填的话 auction 池的
    patterns 字段恒为空，「诱多出货是不是更差」就永远无法审计。
    """
    # _run 已固定 record=False（测试绝不写留痕），所以这里应当拿到空列表
    out = _run(30)
    assert out["recorded_codes"] == []
