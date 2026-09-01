"""规则生命周期汇总的性质测试。

重点不是「某条规则该在哪一档」——那是人做的处置，写死在 RULE_STAGES 里，测它等于
把常量抄一遍。测的是这张表赖以成立的三条规矩：判定取产品口径、没处置的要被显出来、
池子变体不能混进规则表。
"""
import json

from quantcore.quant.rule_lifecycle import build_lifecycle


def _write(tmp_path, name, entry, results, generated_at="2026-08-31T10:00:00"):
    payload = {"since": "2020-01-02", "horizon": 5, "entry": entry,
               "generated_at": generated_at, "results": results}
    (tmp_path / f"rule-audit-{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _res(rule, **kw):
    base = {"rule": rule, "samples": 1000, "clusters": 100, "avg_excess": 0.5,
            "inc_excess": 0.2, "inc_ci_lo": 0.05, "inc_ex_tail": 0.1,
            "stable_years": 5, "passed": False, "failed_gates": []}
    base.update(kw)
    return base


def test_product_entry_wins_over_close(tmp_path):
    """同一条规则既有 close 又有 open 的结果时，主判定必须取 open。

    产品是次日开盘买入，close 口径含一段用户吃不到的隔夜收益——donchian 那条规则
    两个口径分别是 −0.09 和 −0.47。挑错口径会让「已复核」这种结论凭空成立。
    """
    _write(tmp_path, "20260831-120000", "open", [_res("foo", inc_excess=-0.47)])
    # close 那份时间更近，但口径不对，不该被选为主判定
    _write(tmp_path, "20260831-130000", "close", [_res("foo", inc_excess=-0.09)])
    by = {it["rule"]: it for it in build_lifecycle(str(tmp_path))["items"]}
    assert by["foo"]["latest"]["entry"] == "open"
    assert by["foo"]["latest"]["inc_excess"] == -0.47
    assert by["foo"]["audits"] == 2


def test_missing_entry_is_reported_not_guessed(tmp_path):
    """老结果没记入场口径时如实标未记录，不能默认当成某一种。"""
    payload = {"since": "2020-01-02", "horizon": 5, "results": [_res("bar")]}
    (tmp_path / "rule-audit-old.json").write_text(json.dumps(payload), encoding="utf-8")
    by = {it["rule"]: it for it in build_lifecycle(str(tmp_path))["items"]}
    assert by["bar"]["latest"]["entry"] == "未记录"


def test_audited_but_undisposed_rules_are_surfaced(tmp_path):
    """跑过审计却没登记处置的，必须落进「已审待处置」而不是混在「待审」里。

    这一档是整张表的用处所在：它就是「上轮审完忘了收尾」的清单。若把它和从没审过的
    规则并成一档，这份清单立刻失去意义。
    """
    _write(tmp_path, "20260831-120000", "open", [_res("brand_new", passed=True)])
    out = build_lifecycle(str(tmp_path))
    by = {it["rule"]: it for it in out["items"]}
    assert by["brand_new"]["stage"] == "unassigned"
    assert out["counts"]["unassigned"] >= 1
    # 登记过处置的规则（内置常量里就有）不该被算进待处置
    assert by["sector_hot"]["stage"] == "production"
    assert by["sector_hot"]["stage_note"]


def test_pool_variant_rows_are_not_rules(tmp_path):
    """回放变体行（smart:base,-chase20 这种）不是规则，不能进这张表。"""
    _write(tmp_path, "20260831-120000", "open",
           [_res("smart:base,-chase20"), _res("pool:strength"), _res("real_rule")])
    names = {it["rule"] for it in build_lifecycle(str(tmp_path))["items"]}
    assert "real_rule" in names
    assert not any(":" in n for n in names)


def test_broken_result_file_is_skipped(tmp_path):
    """结果目录里有坏文件时跳过即可，不能让整张表打不开。"""
    (tmp_path / "rule-audit-broken.json").write_text("{not json", encoding="utf-8")
    _write(tmp_path, "20260831-120000", "open", [_res("ok_rule")])
    names = {it["rule"] for it in build_lifecycle(str(tmp_path))["items"]}
    assert "ok_rule" in names


def test_missing_directory_returns_empty_table(tmp_path):
    """结果目录不存在时返回空表，不抛栈——这张表挂了不该影响任何选股链路。"""
    out = build_lifecycle(str(tmp_path / "nope"))
    assert out["runs"] == 0
    # 内置的处置登记仍在，只是没有机器判定
    assert all(it["latest"] is None for it in out["items"])


def test_comparable_only_lists_same_run_rules(tmp_path):
    """`comparable` 必须只列出与本条判定同出一次审计的规则。

    这是 2026-08-31 一次误判的回归测试：当时照着表把 sector_hot5 (+0.21) 读成优于
    生产口径 sector_hot (+0.145)，但两个数来自不同批次——Holm 家族不同、入场口径也
    不同。六条同批重跑后 sector_hot5 只有 +0.11，是过闸里最弱的。表本身不能防止人
    横向比，但它必须说清楚谁跟谁可比。
    """
    _write(tmp_path, "20260831-100000", "open", [_res("a"), _res("b")])
    _write(tmp_path, "20260831-110000", "open", [_res("c")])
    by = {it["rule"]: it for it in build_lifecycle(str(tmp_path))["items"]}
    assert by["a"]["comparable"] == ["b"]
    assert by["b"]["comparable"] == ["a"]
    assert by["c"]["comparable"] == []
    assert by["a"]["verdict_run"] == by["b"]["verdict_run"]
    assert by["c"]["verdict_run"] != by["a"]["verdict_run"]


def test_comparable_follows_the_chosen_verdict_not_any_shared_run(tmp_path):
    """两条规则在旧批次里同框、但主判定各取自不同批次时，不能算可比。"""
    # 旧批次里 x 和 y 同框
    _write(tmp_path, "20260830-100000", "close", [_res("x"), _res("y")])
    # x 后来单独用产品口径重审过 → 主判定换到这一份，与 y 不再同批
    _write(tmp_path, "20260831-100000", "open", [_res("x")])
    by = {it["rule"]: it for it in build_lifecycle(str(tmp_path))["items"]}
    assert by["x"]["latest"]["entry"] == "open"
    assert by["x"]["comparable"] == []
    assert by["y"]["comparable"] == []
