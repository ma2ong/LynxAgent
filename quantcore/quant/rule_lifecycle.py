"""选股规则的生命周期：每条规则现在处在哪一档，凭什么。

为什么需要这个文件
------------------
系统里一条规则此前只有两种存在方式：要么写在生产评分里，要么躺在 experiments/ 的
某个脚本里。中间那段——「审过了、没过闸、以后别再试」——没有任何地方承载，于是同一批
想法被反复重新提出、重新验证：强势池、龙虎榜、缩量埋伏、主升浪、追高闸门、形态族，
每一轮都要把「这个我们试过了，结论是负的」重新讲一遍，还得靠人记得住。

这里把两样东西合到一起：
  · 机器给的**判定**——直接读 experiments/results/ 里的审计结果，不重算、不解释；
  · 人给的**处置**——RULE_STAGES 里的档位，是看过判定之后做的决定。
两者分开是刻意的：判定该由尺子说了算，处置该由人负责。混在一起就会变成「我觉得它
应该有用所以让它上线」，那正是这套闸门要防的事。

档位的含义
----------
production  已进生产排序。改它要先跑头对头。
observation 只在界面上如实标注，不参与排序。适用于「统计上确有方向、但过不了全部闸门」。
rejected    审计未过且不再重试。再有人提起，先来这里看一眼上次为什么否。
candidate   已写成规则但还没跑过审计。
unassigned  **机器已经给了判定，人还没做处置。** 这一档不是摆设：它就是「上次审完
            忘了收尾」的清单，表上有几条就说明有几个结论还没落到任何地方。

跨 run 的数字不能横向比
----------------------
本表按规则各取一条「最近一次判定」，这些判定往往来自**不同批次**的审计：Holm 家族
不同、入场口径可能不同、样本起点也可能不同。把它们并排读会得出错误结论 —— 2026-08-31
就发生过：表上 sector_hot5 (+0.21) 看着优于生产用的 sector_hot (+0.145)，六条同批
重跑后 sector_hot5 只有 +0.11，是过闸里最差的那条，而现行口径反而最好。

所以：**要比较两条规则，必须让它们出现在同一次 `--rule a --rule b` 的提交里。**
本表只回答「这条规则自己审出来什么」，不回答「哪条更好」。`comparable` 字段标出
哪些规则共享同一次审计，界面据此提示。

池子级的概念（强势池这种）也可以登记在这里。它们在审计结果里以 `pool:` / `变体`
形式出现、不会被当成规则读进来，但「试过、否了」这件事同样需要一个地方记住。
"""
from __future__ import annotations

import glob
import json
import os
from typing import Any, Dict, List

# experiments 不属于生产包，但审计结果是唯一的判定来源，所以这里按路径读，
# 读不到就返回空——生命周期表不可用不该影响任何选股链路。
_HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", "experiments", "results"))

# 档位与理由。改这里等于做一次处置决定，请连同理由一起写。
RULE_STAGES: Dict[str, tuple] = {
    # —— 已进生产 ——
    "sector_hot": ("production",
                   "20 日板块动量。唯一独立过全部七闸的规则，也是现行 industry_heat 的主导项。"),
    "sector_stage_new": ("production",
                         "生产 industry_heat 的现行口径（2026-08-28 重配系数，mom20 主导）。"),

    # —— 只标注不排序 ——
    "chase_near_high": ("observation",
                        "一键智选实际买的就是这类票，增量为负但七个候选闸门剂量反应不单调，"
                        "改为在界面如实标注画像，不动排序。"),
    "chase_in_cold": ("observation",
                      "冷环境追高：增量 −1.05、CI 上界仍为负、剂量单调，是除板块动量外最强的"
                      "闸门候选；但去右尾后翻正（亏的是错过反弹而非跌得更多），过不了右尾闸。"
                      "样本更足时复审。"),

    # —— 已否决，别再重试 ——
    "strength": ("rejected", "强势池四条硬筛全非约束，实际只剩「买最极端」，同口径 T+5 为负。"),
    "dryup": ("rejected", "个股缩量侧无信息；相关的放量负信号增益全在收盘→次日开盘那一段。"),
    "dryup_strong": ("rejected", "同上，加趋势条件后依然不过。"),
    "dryup_washout": ("rejected", "同上，加洗盘形态后依然不过。"),
    "dryup_allen": ("rejected", "地量族加满全部条件仍不过，逐条加码没有出现拐点。"),
    "dryup_allen_nosec": ("rejected", "同上，拆掉板块条件后仍不过。"),
    "sector_leader": ("rejected", "强板块里的龙头，增量不显著。"),
    "sector_laggard": ("rejected", "强板块里的补涨位，增量不显著。"),
    "handover": ("rejected", "板块龙头易主，增量不显著。"),
    "sector_volexp": ("rejected", "板块量能扩张（曾是生产 industry_heat 的主导项），实测无 alpha，已重配权重。"),
    "sector_stage": ("rejected",
                     "板块阶段分旧口径。六条同批复核（产品口径、1586 天）增量 +0.11，"
                     "劣于现行 sector_stage_new 的 +0.17，已被取代。"),
    "sector_hot5": ("rejected",
                    "板块 5 日强度。曾因跨 run 比较被误读成优于现行口径；六条同批复核后"
                    "增量 +0.11、CI 下沿 +0.02、去右尾 +0.19，是过闸五条里最弱的。窗口用 5 日不如 20 日。"),
    "sector_hot_mix": ("rejected",
                       "5 日与 20 日强度的混合档。同批复核 +0.15，仍低于现行 sector_stage_new 的 +0.17，"
                       "换口径没有收益，不动。"),
    "sector_accel": ("rejected", "板块刚启动（5 日强、20 日未起），增量为负。"),
    "consolidate": ("rejected", "长期强 + 近期消化，增量为负且去右尾更差。"),
    "deep_drop": ("rejected", "近 20 日深跌，样本/增量都不支持。"),
    "deep_drop_vol": ("rejected", "深跌 + 放量强收：增量为正但未过全部闸门，未达上线标准。"),
    "hot_dryup": ("rejected", "强板块 + 个股地量的交集，增量为负。"),
    "chase15": ("rejected", "追高闸门剂量族：三档增量全为负且不单调，作为闸门的七个候选全否。"),
    "chase20": ("rejected", "同上，25% 档。"),
    "chase35": ("rejected", "同上，35% 档。"),
    "near_high": ("rejected", "只看「贴近 20 日高点」不带涨幅条件，增量为负。"),
    "chop_trend": ("rejected", "震荡/趋势判别：双向增量都为负，说明该维度本身无区分力，非方向选反。"),
    "chop_range": ("rejected", "同上，另一侧。"),
    "supertrend_up": ("rejected", "ATR 通道方向，增量为负。"),
    "supertrend_flip": ("rejected", "通道翻转时点，增量为负且去右尾更差。"),
    "donchian20": ("rejected", "20 日新高突破：收盘口径 −0.09，产品口径 −0.47，收益全在隔夜跳空里。"),
    "donchian55": ("rejected", "同上，55 日档，剂量一致为负。"),
    "maxvol_down": ("rejected", "天量收阴（疑似派发），方向对但幅度扣不动摩擦。"),
    "maxvol_up": ("rejected", "天量收阳，同上。"),
}

STAGE_LABEL = {
    "production": "已进排序",
    "observation": "只标注",
    "unassigned": "已审待处置",
    "candidate": "待审",
    "rejected": "已否决",
}
# 「已审待处置」排在前面：它是唯一一档需要人去做点什么的
STAGE_ORDER = ["production", "observation", "unassigned", "candidate", "rejected"]


def _load_runs(results_dir: str) -> List[Dict[str, Any]]:
    """把 results 目录里的审计结果按时间从新到旧读进来。坏文件跳过，不中断。"""
    runs: List[Dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(results_dir, "rule-audit-*.json")), reverse=True):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        data["_file"] = os.path.basename(path)
        runs.append(data)
    return runs


def _record(run: Dict[str, Any], res: Dict[str, Any]) -> Dict[str, Any]:
    """一次审计里针对一条规则的那一行，只留判定需要的字段。"""
    return {
        "file": run.get("_file"),
        "generated_at": run.get("generated_at"),
        "since": run.get("since"),
        "horizon": run.get("horizon"),
        # 老结果没记入场口径（这个字段是后加的）。不猜，如实标未知——
        # 把它当成 close 会让「产品口径复核过了」这种结论凭空成立。
        "entry": run.get("entry") or "未记录",
        "samples": res.get("samples"),
        "clusters": res.get("clusters"),
        "avg_excess": res.get("avg_excess"),
        "inc_excess": res.get("inc_excess"),
        "inc_ci_lo": res.get("inc_ci_lo"),
        "inc_ex_tail": res.get("inc_ex_tail"),
        "stable_years": res.get("stable_years"),
        "passed": bool(res.get("passed")),
        "failed_gates": res.get("failed_gates") or [],
    }


def build_lifecycle(results_dir: str = RESULTS_DIR) -> Dict[str, Any]:
    """汇总每条规则的最新判定 + 历次审计，配上人给的档位。

    「最新」按产品口径优先：同一条规则若既有 open 又有 close 的结果，取最近一次
    open 的作为主判定 —— 产品是次日开盘买入，close 口径含一段用户吃不到的隔夜收益。
    """
    runs = _load_runs(results_dir)
    latest: Dict[str, Dict[str, Any]] = {}
    history: Dict[str, List[Dict[str, Any]]] = {}
    for run in runs:
        for res in run.get("results") or []:
            name = str(res.get("rule") or "")
            # 池子/变体的审计行（形如 "smart:base,-chase20"）不是规则，不进这张表
            if not name or ":" in name:
                continue
            rec = _record(run, res)
            history.setdefault(name, []).append(rec)
            cur = latest.get(name)
            if cur is None or (rec["entry"] == "open" and cur["entry"] != "open"):
                latest[name] = rec

    items: List[Dict[str, Any]] = []
    for name in sorted(set(latest) | set(RULE_STAGES)):
        rec = latest.get(name)
        if name in RULE_STAGES:
            stage, note = RULE_STAGES[name]
        else:
            # 没登记处置的：跑过审计的落「已审待处置」，没跑过的才是「待审」。
            # 不按判定自动归档 —— 判定归机器，处置归人，自动化这一步就等于取消了这一步。
            stage = "unassigned" if rec else "candidate"
            note = ""
        # 与本条判定同出一次审计的其他规则：只有这些才可以和它横向比
        comparable = sorted(
            other for other, rec2 in latest.items()
            if rec and rec2 and other != name and rec2["file"] == rec["file"]
        ) if rec else []
        items.append({
            "rule": name,
            "stage": stage,
            "verdict_run": rec["file"] if rec else None,
            "comparable": comparable,
            "stage_label": STAGE_LABEL.get(stage, stage),
            "stage_note": note,
            "audits": len(history.get(name, [])),
            "latest": rec,
            "history": history.get(name, [])[:6],
        })
    items.sort(key=lambda it: (STAGE_ORDER.index(it["stage"]) if it["stage"] in STAGE_ORDER else 9,
                               it["rule"]))

    counts: Dict[str, int] = {s: 0 for s in STAGE_ORDER}
    for it in items:
        counts[it["stage"]] = counts.get(it["stage"], 0) + 1
    return {
        "results_dir": results_dir,
        "runs": len(runs),
        "counts": counts,
        "stage_label": STAGE_LABEL,
        "items": items,
    }
