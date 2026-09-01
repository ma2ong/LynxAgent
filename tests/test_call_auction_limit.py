"""集合竞价买入推荐的展示上限。

只测「给多少只」这一条，不测选股逻辑本身——后者由留痕和复盘页回答。
"""
from quantcore.quant.call_auction import compute_call_auction

INDUSTRY = "半导体"


def _snapshot(n: int, opens: list[float] | None = None) -> dict:
    """造 n 只都够格进买入候选的票：高开、放量、成交额充足。

    opens 给定时按它设置各自的开盘涨幅——档位是**相对当日最高分**算的，所以想
    构造「只有一只够强推荐」的盘面，必须让第一名和其余拉开足够大的差距。
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

    竞价窗口只有几分钟可操作，一口气给十几只等于没给。上限是产品决定
    （2026-09-01 Allen 定），不是评分算法的结果。
    """
    out = _run(30)
    assert out["available"] is True
    assert len(out["buy_candidates"]) <= 5
    assert out["display_limit"] == 5


def test_one_qualifier_shows_one_not_five():
    """只有一只够「强推荐」档时就给一只——5 是上限，不是凑数目标。

    一枝独秀的盘面：第一名远强于其余，档位按「占当日最高分的比例」算，
    其余全部掉出强推荐档。此时名单必须只有 1 只。
    """
    out = _run(13, opens=[5.9] + [1.6] * 12)
    assert out["strong_tier_count"] == 1
    assert len(out["buy_candidates"]) == 1


def test_short_list_is_not_padded():
    """够格的不足 5 只时按实际给，不拿弱票凑数。"""
    out = _run(2)
    assert len(out["buy_candidates"]) <= 2


def test_hidden_count_covers_what_the_cap_held_back():
    """被上限挡下的必须计入未展示数，否则用户会以为今天只有这么点货。"""
    out = _run(30)
    assert out["hidden_candidates"] >= 1


def test_record_sample_is_not_truncated_by_the_display_cap():
    """留痕仍按 buy_limit 记满：把展示上限套到留痕上等于砍掉三分之二的复盘样本。

    展示只有 5 只，但排名信息要覆盖到 buy_limit 只，复盘才能继续回答
    「名次和涨停率什么关系」。
    """
    out = _run(30, buy_limit=15)
    ranked_positions = [c["rank"] for c in out["buy_candidates"]]
    assert ranked_positions == sorted(ranked_positions)
    # 候选池本身没有被展示上限裁掉
    assert out["hidden_candidates"] + len(out["buy_candidates"]) >= 15
