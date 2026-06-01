import json
import logging
import re

logger = logging.getLogger(__name__)

try:
    import akshare as ak
except ImportError:
    ak = None

INDUSTRY_PROMPT = """你是一名资深产业链研究员。对股票 {code}（{name}）进行产业链分析。

请严格以 JSON 格式输出，不要加任何 markdown 标记：
{{
  "chain": {{
    "upstream": [{{"name": "原材料/供应商类别", "companies": ["代表公司1"]}}],
    "midstream": [{{"name": "本公司所在环节", "companies": ["{name}"]}}],
    "downstream": [{{"name": "下游客户类别", "companies": ["代表公司1"]}}]
  }},
  "position": "upstream/midstream/downstream",
  "moat": "核心竞争壁垒描述（1-2句话）"
}}

只做定性分析，不需要输出估值数字。"""


def _fetch_real_peers(code: str, max_peers: int = 5) -> list:
    """Fetch real PE/PB for same-industry peers from akshare. Returns [] on any failure."""
    if ak is None:
        return []
    try:
        import pandas as pd

        # Step 1: Get the stock's industry board name
        info = ak.stock_individual_info_em(symbol=code)
        if info is None or info.empty:
            return []
        industry_row = info[info.iloc[:, 0].astype(str).str.contains("行业|板块", na=False)]
        if industry_row.empty:
            return []
        board_name = str(industry_row.iloc[0, 1]).strip()

        # Step 2: Get all stocks in that industry board with PE/PB data
        spot_df = ak.stock_board_industry_spot_em(symbol=board_name)
        if spot_df is None or spot_df.empty:
            return []

        code_col = next((c for c in ["代码", "股票代码"] if c in spot_df.columns), None)
        name_col = next((c for c in ["名称", "股票名称"] if c in spot_df.columns), None)
        pe_col = next((c for c in ["市盈率-动态", "市盈率", "PE"] if c in spot_df.columns), None)
        pb_col = next((c for c in ["市净率", "PB"] if c in spot_df.columns), None)

        if not code_col:
            return []

        peers = []
        for _, row in spot_df.iterrows():
            peer_code = str(row[code_col]).strip().zfill(6)
            if peer_code == code.strip().zfill(6):
                continue
            pe_val = float(row[pe_col]) if pe_col and pd.notna(row[pe_col]) else 0.0
            pb_val = float(row[pb_col]) if pb_col and pd.notna(row[pb_col]) else 0.0
            peers.append({
                "code": peer_code,
                "name": str(row[name_col]) if name_col else peer_code,
                "pe": pe_val,
                "pb": pb_val,
            })
            if len(peers) >= max_peers:
                break
        return peers
    except Exception:
        return []


class IndustryAnalystAgent:
    def __init__(self, llm_client):
        self.llm_client = llm_client

    def analyze(self, code: str, name: str) -> dict:
        prompt = INDUSTRY_PROMPT.format(code=code, name=name)
        error_key = None
        try:
            response = self.llm_client.chat(prompt)
            response = re.sub(r"^```\w*\s*", "", response.strip())
            response = re.sub(r"```\s*$", "", response).strip()
            parsed = json.loads(response)
        except json.JSONDecodeError as e:
            logger.warning(f"IndustryAgent JSON parse error for {code}: {e}")
            parsed = {"chain": {}, "peers": [], "position": "unknown", "moat": ""}
            error_key = str(e)
        except Exception as e:
            logger.warning(f"IndustryAgent LLM error for {code}: {e}")
            parsed = {"chain": {}, "peers": [], "position": "unknown", "moat": ""}
            error_key = str(e)

        real_peers = _fetch_real_peers(code)
        parsed["peers"] = real_peers if real_peers else parsed.get("peers", [])

        if error_key is not None:
            parsed["error"] = error_key

        return parsed
