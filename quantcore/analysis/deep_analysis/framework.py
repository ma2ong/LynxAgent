import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import polars as pl
from quantcore.data.sources.akshare_adapter import AKShareAdapter
from quantcore.data.pipeline.cache_manager import CacheManager
from quantcore.analysis.deep_analysis.valuation import ValuationCalculator
from quantcore.analysis.deep_analysis.scenario import ScenarioAnalysis
from quantcore.analysis.deep_analysis.industry_agent import IndustryAnalystAgent

logger = logging.getLogger(__name__)

MACRO_PROMPT = "从宏观视角分析{name}({code})所处行业景气度和政策环境，50字以内。"
QUALITY_PROMPT = """对{name}({code})打分（0-100），输出纯JSON：
{{"fundamental":分数,"governance":分数,"competitive":分数,"growth":分数,"valuation":分数,"rationale":"1句话理由"}}"""
RISK_PROMPT = "列出{name}({code})最主要的3个研究风险和对应失效条件，格式为JSON数组：[{{\"risk\":\"...\",\"mitigation\":\"...\"}}]"
TRACKING_PROMPT = "为{name}({code})制定跟踪计划：关键财务指标阈值和下次复盘建议，JSON格式：{{\"metrics\":[{{\"name\":\"...\",\"threshold\":\"...\"}}],\"next_review\":\"Q几财报季\"}}"
RATING_PROMPT = """基于以下分析，给出综合信号（必须是：积极关注/继续跟踪/观察/回避 之一）：
质量评分: {quality}
估值分位: {valuation_percentile}
风险数量: {risk_count}
只输出信号词，不要其他内容。"""


def _strip_md(text: str) -> str:
    text = re.sub(r"^```\w*\s*", "", text.strip())
    return re.sub(r"```\s*$", "", text).strip()


class DeepAnalysisFramework:

    def __init__(self, llm_client, fast_llm_client=None):
        self.llm = llm_client
        self.fast_llm = fast_llm_client if fast_llm_client is not None else llm_client
        self.valuation_calc = ValuationCalculator()
        self.scenario_engine = ScenarioAnalysis()
        self.industry_agent = IndustryAnalystAgent(llm_client)

    def analyze(self, code: str, name: str) -> dict:
        adapter = AKShareAdapter()
        cache = CacheManager()

        with ThreadPoolExecutor(max_workers=6) as pool:
            f_daily = pool.submit(self._get_daily, code, adapter, cache)
            f_fin = pool.submit(self._get_financial, code, adapter)
            f_macro = pool.submit(self.llm.chat, MACRO_PROMPT.format(code=code, name=name))
            f_quality = pool.submit(self.llm.chat, QUALITY_PROMPT.format(code=code, name=name))
            f_risk = pool.submit(self.llm.chat, RISK_PROMPT.format(code=code, name=name))
            f_industry = pool.submit(self.industry_agent.analyze, code, name)

            daily_df = f_daily.result()
            fin_df = f_fin.result()
            macro = f_macro.result()
            industry_result = f_industry.result()

            quality_raw = _strip_md(f_quality.result())
            try:
                quality_score = json.loads(quality_raw)
            except json.JSONDecodeError:
                quality_score = {"fundamental": 50, "governance": 50, "competitive": 50,
                                 "growth": 50, "valuation": 50, "rationale": ""}

            risk_raw = _strip_md(f_risk.result())
            try:
                risks = json.loads(risk_raw)
            except json.JSONDecodeError:
                risks = []

        scenarios = self._compute_scenarios(fin_df, daily_df)

        valuation = {}
        if fin_df is not None and not fin_df.is_empty():
            if "pe_ttm" in fin_df.columns:
                pe_bands = self.valuation_calc.compute_valuation_bands(fin_df, "pe_ttm")
                pb_bands = self.valuation_calc.compute_valuation_bands(fin_df, "pb") if "pb" in fin_df.columns else {}
                ps_bands = self.valuation_calc.compute_valuation_bands(fin_df, "ps") if "ps" in fin_df.columns else {}
                valuation = {"pe": pe_bands, "pb": pb_bands, "ps": ps_bands}

        # tracking uses fast_llm (formatting task)
        tracking_raw = _strip_md(self.fast_llm.chat(TRACKING_PROMPT.format(code=code, name=name)))
        try:
            tracking_plan = json.loads(tracking_raw)
        except json.JSONDecodeError:
            tracking_plan = {"metrics": [], "next_review": ""}

        # rating uses fast_llm (classification task)
        numeric_scores = [v for v in quality_score.values() if isinstance(v, (int, float))]
        avg_quality = sum(numeric_scores) / len(numeric_scores) if numeric_scores else 50
        valuation_pct = valuation.get("pe", {}).get("percentile", 0.5)
        rating_raw = self.fast_llm.chat(RATING_PROMPT.format(
            quality=round(avg_quality),
            valuation_percentile=round(valuation_pct, 2),
            risk_count=len(risks),
        )).strip()
        overall_rating = rating_raw if rating_raw in ["积极关注", "继续跟踪", "观察", "回避"] else "观察"

        return {
            "code": code,
            "name": name,
            "macro": macro,
            "industry": industry_result.get("chain", {}),
            "quality_score": quality_score,
            "scenarios": scenarios,
            "risks": risks,
            "valuation": valuation,
            "peers": industry_result.get("peers", []),
            "tracking_plan": tracking_plan,
            "overall_rating": overall_rating,
            "moat": industry_result.get("moat", ""),
            "daily_df": daily_df.to_dicts() if daily_df is not None else [],
        }

    def _get_daily(self, code: str, adapter: AKShareAdapter, cache: CacheManager) -> pl.DataFrame:
        cached = cache.get_cached_daily(code)
        if cached is not None:
            return cached
        end = date.today().strftime("%Y-%m-%d")
        start = (date.today() - timedelta(days=365)).strftime("%Y-%m-%d")
        try:
            return adapter.get_stock_daily(code, start, end)
        except Exception:
            return pl.DataFrame()

    def _get_financial(self, code: str, adapter: AKShareAdapter) -> pl.DataFrame:
        try:
            return adapter.get_financial_indicator(code)
        except Exception:
            return pl.DataFrame()

    def _compute_scenarios(self, fin_df: pl.DataFrame, daily_df: pl.DataFrame | None = None) -> dict:
        if fin_df is None or fin_df.is_empty():
            return {}

        try:
            fin_df = self._sort_financial_rows(fin_df)
            base_revenue = self._latest_number(fin_df, [
                "\u8425\u4e1a\u603b\u6536\u5165", "\u8425\u4e1a\u6536\u5165",
                "revenue", "operating_revenue", "total_operating_revenue",
            ])
            net_profit = self._latest_number(fin_df, [
                "\u51c0\u5229\u6da6", "\u5f52\u6bcd\u51c0\u5229\u6da6",
                "net_profit", "np_parent_company_owners",
            ])
            revenue_growth = self._latest_ratio(fin_df, [
                "\u8425\u4e1a\u603b\u6536\u5165\u540c\u6bd4\u589e\u957f\u7387",
                "\u4e3b\u8425\u4e1a\u52a1\u6536\u5165\u589e\u957f\u7387(%)",
                "revenue_growth",
            ])
            margin = self._latest_ratio(fin_df, [
                "\u9500\u552e\u51c0\u5229\u7387", "\u9500\u552e\u51c0\u5229\u7387(%)", "net_margin"
            ])
            pe_current = self._latest_number(fin_df, ["pe_ttm", "\u5e02\u76c8\u7387", "PE", "pe"])
            shares = self._latest_number(fin_df, [
                "\u603b\u80a1\u672c", "total_share", "total_shares", "shares"
            ])
            eps = self._latest_number(fin_df, [
                "\u57fa\u672c\u6bcf\u80a1\u6536\u76ca", "\u6bcf\u80a1\u6536\u76ca", "eps"
            ])

            if margin is None and base_revenue and net_profit:
                margin = net_profit / base_revenue
            close_price = self._latest_close(daily_df)
            if pe_current is None and close_price and eps:
                pe_current = close_price / eps
            if shares is None and net_profit and pe_current and close_price:
                shares = net_profit * pe_current / close_price
            if shares is None and net_profit and eps:
                shares = net_profit / eps

            if not base_revenue or not shares or margin is None:
                return {}

            growth_base = self._clamp(revenue_growth if revenue_growth is not None else 0.08, -0.20, 0.35)
            growth_bear = self._clamp(growth_base - 0.08, -0.30, 0.20)
            growth_bull = self._clamp(growth_base + 0.08, -0.05, 0.50)
            margin_base = self._clamp(margin, 0.01, 0.45)
            pe_base = self._clamp(pe_current if pe_current else 18.0, 5.0, 80.0)

            scenarios = self.scenario_engine.compute(
                base_revenue=base_revenue,
                revenue_growth_bear=growth_bear,
                revenue_growth_base=growth_base,
                revenue_growth_bull=growth_bull,
                margin_bear=max(0.01, margin_base * 0.80),
                margin_base=margin_base,
                margin_bull=min(0.45, margin_base * 1.15),
                pe_bear=max(5.0, pe_base * 0.80),
                pe_base=pe_base,
                pe_bull=min(80.0, pe_base * 1.20),
                shares=shares,
            )
            scenarios["assumptions"] = {
                "base_revenue": round(base_revenue, 4),
                "margin_base": round(margin_base, 4),
                "revenue_growth_base": round(growth_base, 4),
                "pe_base": round(pe_base, 4),
                "shares": round(shares, 4),
                "source": "financial_statement",
            }
            return scenarios
        except Exception:
            return {}

    def _sort_financial_rows(self, fin_df: pl.DataFrame) -> pl.DataFrame:
        for column in ["\u62a5\u544a\u671f", "\u65e5\u671f", "date", "trade_date"]:
            if column in fin_df.columns:
                return fin_df.sort(column, descending=True)
        return fin_df

    def _latest_number(self, fin_df: pl.DataFrame, columns: list[str]) -> float | None:
        for column in columns:
            if column not in fin_df.columns:
                continue
            for value in fin_df[column].to_list():
                number = self._parse_number(value)
                if number is not None:
                    return number
        return None

    def _latest_ratio(self, fin_df: pl.DataFrame, columns: list[str]) -> float | None:
        for column in columns:
            if column not in fin_df.columns:
                continue
            for value in fin_df[column].to_list():
                number = self._parse_number(value)
                if number is None:
                    continue
                if isinstance(value, str) and "%" in value:
                    return number / 100
                return number / 100 if abs(number) > 1 else number
        return None

    def _latest_close(self, daily_df: pl.DataFrame | None) -> float | None:
        if daily_df is None or daily_df.is_empty() or "close" not in daily_df.columns:
            return None
        for value in reversed(daily_df["close"].to_list()):
            number = self._parse_number(value)
            if number is not None and number > 0:
                return number
        return None

    def _parse_number(self, value) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        if not text or text.lower() in {"nan", "none", "false", "--", "-"}:
            return None
        multiplier = 1.0
        if text.endswith("\u4ebf"):
            multiplier = 100_000_000.0
            text = text[:-1]
        elif text.endswith("\u4e07"):
            multiplier = 10_000.0
            text = text[:-1]
        text = text.replace(",", "").replace("%", "")
        try:
            return float(text) * multiplier
        except ValueError:
            return None

    def _clamp(self, value: float, lower: float, upper: float) -> float:
        return max(lower, min(upper, value))
