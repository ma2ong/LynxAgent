"""集合竞价四形态识别：主力抢筹/诱多出货/洗盘低吸/多空分歧。

轨迹取自东财盘前分时（09:15-09:25 逐分钟虚拟撮合价），竞价段无成交量，故只用价格判形态。
"""
import requests

from quantcore.quant.auction_tape import (
    classify_trajectory,
    fetch_auction_trend,
    gate_candidates,
    tape_summary,
)


def _traj(prices):
    """[(hh:mm, 虚拟撮合价), …]，09:15 起逐分钟。"""
    return [(f"09:{15 + i:02d}", p) for i, p in enumerate(prices)]


def test_fetch_falls_back_when_primary_endpoint_is_unreachable(monkeypatch):
    """主 HTTPS 域名被代理/运营商断开时，必须继续尝试可用镜像，不能把整池判成数据不足。"""
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "preClose": 10.0,
                    "trends": [
                        "2026-07-24 09:15,10.0,10.0,10.0,10.0,0,0,10.0",
                        "2026-07-24 09:16,10.2,10.1,10.2,10.1,0,0,10.0",
                        "2026-07-24 09:17,10.3,10.2,10.3,10.2,0,0,10.0",
                        "2026-07-24 09:18,10.4,10.3,10.4,10.3,0,0,10.0",
                    ],
                }
            }

    class Session:
        trust_env = True

        def get(self, url, **kwargs):
            calls.append((url, self.trust_env))
            if len(calls) == 1:
                raise requests.ConnectionError("primary unavailable")
            return Response()

    monkeypatch.setattr("quantcore.quant.auction_tape.requests.Session", Session)
    prev_close, points = fetch_auction_trend("600000")

    assert prev_close == 10.0
    assert points == [("09:16", 10.1), ("09:17", 10.2), ("09:18", 10.3)]
    assert len(calls) == 2
    assert calls[0][1] is True
    assert calls[1][1] is False


def test_insufficient_samples():
    """有效报价不足 3 分钟 → 判不出形态（如全天无人挂单的僵尸票）。"""
    res = classify_trajectory(_traj([10.0, 10.1]), 10.0)
    assert res["pattern"] == "insufficient"


def test_accumulation():
    """临时价一路走高且收在昨收之上 → 主力抢筹。"""
    res = classify_trajectory(_traj([10.1, 10.2, 10.35, 10.5, 10.6]), 10.0)
    assert res["pattern"] == "accumulation"
    assert res["drift"] > 0 and res["gap_last"] > 0


def test_distribution_bull_trap():
    """明显高开(+3%)后被砸回 → 诱多出货。"""
    res = classify_trajectory(_traj([10.3, 10.2, 10.1, 10.0, 9.95]), 10.0)
    assert res["pattern"] == "distribution"
    assert res["label"] == "诱多出货"
    assert res["gap_open"] >= 2.0 and res["drift"] < 0


def test_distribution_dive_without_high_open():
    """没怎么高开就一路跳水，同样是抛压主导——旧判据要求先高开 2% 才认，会漏掉。

    真实样例：中芯国际 2026-07-22 竞价 +0.63% -> -2.45%。
    """
    res = classify_trajectory(_traj([160.0, 160.0, 158.5, 158.0, 156.1]), 159.0)
    assert res["pattern"] == "distribution"
    assert res["label"] == "竞价跳水"


def test_shakeout():
    """先探底再明显回升 → 洗盘低吸（与一路走高的抢筹区分）。"""
    res = classify_trajectory(_traj([10.0, 9.9, 9.85, 10.1, 10.3]), 10.0)
    assert res["pattern"] == "shakeout"


def test_divergence_choppy():
    """价格反复上下拉锯、最终回到起点附近 → 多空分歧。"""
    res = classify_trajectory(_traj([10.2, 10.0, 10.25, 10.05, 10.22, 10.15]), 10.0)
    assert res["pattern"] == "divergence"
    assert res["reversals"] >= 3


def test_decisive_drift_beats_choppiness():
    """震荡但明确被砸下来 → 诱多出货，不能因为反转次数多就判成"分歧"而放过。

    真实样例：中科飞测 2026-07-22 竞价 +5.06% -> +2.60%，中途 3 次反转。
    """
    res = classify_trajectory(_traj([10.5, 10.3, 10.4, 10.2, 10.3, 10.26]), 10.0)
    assert res["pattern"] == "distribution"


def test_dead_cat_bounce_is_not_shakeout():
    """崩塌途中的反抽不是洗盘低吸：末价必须收回起点附近才算。

    真实样例：东芯股份 2026-07-22 竞价 +3.06% -> 探底 -> -0.38%。
    """
    res = classify_trajectory(_traj([10.31, 10.05, 9.81, 9.9, 9.96]), 10.0)
    assert res["pattern"] == "distribution"


def test_flat_drift_is_neutral():
    """小幅低走但不到跳水阈值 → 方向不明，不硬套形态。

    真实样例：贵州茅台 2026-07-22 竞价 -0.34% -> -0.61%。
    """
    res = classify_trajectory(_traj([1303.6, 1299.0, 1295.0, 1295.0, 1300.0]), 1308.0)
    assert res["pattern"] == "neutral"


def test_summary_counts_only_given_candidates():
    """概况只统计传入的候选池，available 要求至少判出一只。"""
    results = {
        "600000": classify_trajectory(_traj([10.1, 10.2, 10.35, 10.5, 10.6]), 10.0),
        "600001": classify_trajectory(_traj([10.0, 10.1]), 10.0),  # insufficient
    }
    summary = tape_summary(results)
    assert summary["tracked"] == 2
    assert summary["resolved"] == 1
    assert summary["pattern_counts"]["accumulation"] == 1
    assert summary["available"] is True

    assert tape_summary({"600001": results["600001"]})["available"] is False


def test_candidate_gate_only_keeps_bullish_auction_patterns():
    candidates = [
        {"code": "600001", "rank": 1},
        {"code": "600002", "rank": 2},
        {"code": "600003", "rank": 3},
        {"code": "600004", "rank": 4},
    ]
    patterns = {
        "600001": {"pattern": "accumulation"},
        "600002": {"pattern": "shakeout"},
        "600003": {"pattern": "distribution"},
        "600004": {"pattern": "insufficient"},
    }

    allowed, rejected = gate_candidates(candidates, patterns)

    assert [item["code"] for item in allowed] == ["600001", "600002"]
    assert [item["rank"] for item in allowed] == [1, 2]
    assert [item["code"] for item in rejected] == ["600003", "600004"]
    assert rejected[0]["auction_pattern"]["pattern"] == "distribution"
