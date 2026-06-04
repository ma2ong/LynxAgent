---
name: licensing-fork-origin
description: "TradingAgents project licensing — app/ and frontend/ are hsliuping's proprietary code; what can/can't be reused commercially"
metadata: 
  node_type: memory
  type: project
  originSessionId: f0cd7f38-3baa-46a6-ba28-a9e556a9c2cf
---

The `TradingAgents` repo is a二次 fork: upstream TradingAgents (Tauric Research, Apache-2.0) → TradingAgents-CN (hsliuping, fork) → Allen's fork. Git history: hsliuping 1168 commits, ma2ong (Allen) 88, Tauric ~38.

License split (per root LICENSE, a "mixed license"):
- `tradingagents/` core = **Apache 2.0** (Tauric + hsliuping). Reusable/commercial OK if LICENSE + NOTICE attribution retained for BOTH copyright holders.
- `app/` (FastAPI) and `frontend/` (Vue) = **PROPRIETARY, hsliuping, "all rights reserved"** (`app/LICENSE`, `frontend/LICENSE`): NO redistribution / NO modification / NO commercial use / personal use only. Created by hsliuping (Aug 2025); ~90% of lines are his.

**Implication:** Allen cannot legally publish/commercialize `app/`+`frontend/` as-is — his work on them is derivative of proprietary code. For commercial SaaS he must either license from hsliuping (hsliup@163.com) or rebuild app/frontend clean. See [[quant-lite-clean-repo]].

**Backup repo (2026-06-02):** the full TradingAgents working tree was pushed to **https://github.com/ma2ong/TradingAgents** — kept **PRIVATE on purpose** because it contains hsliuping's proprietary app/+frontend/. ⚠️ NEVER make this repo public (would be public redistribution of hsliuping's proprietary code = infringement). Branch `integration/tradingagents-saas-lite` (also the default), snapshot commit a4ace66 on top of the 1313-commit history. This is distinct from the PUBLIC clean repo **LynxAgent** ([[quant-lite-clean-repo]]) — do NOT mix files between them. Before pushing this repo, verified `.env`/`node_modules`/secrets are gitignored (`.env.docker`/`.env.example` are placeholder templates, safe).
