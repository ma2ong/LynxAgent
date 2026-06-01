class ScenarioAnalysis:

    def compute(
        self,
        base_revenue: float,
        revenue_growth_bear: float,
        revenue_growth_base: float,
        revenue_growth_bull: float,
        margin_bear: float,
        margin_base: float,
        margin_bull: float,
        pe_bear: float,
        pe_base: float,
        pe_bull: float,
        shares: float,
    ) -> dict:
        if shares <= 0:
            raise ValueError("shares must be positive")

        def _calc(growth, margin, pe):
            revenue = round(base_revenue * (1 + growth), 2)
            net_profit = round(revenue * margin, 2)
            eps = round(net_profit / shares, 4)
            target_price = round(eps * pe, 2)
            return {
                "revenue": revenue,
                "net_profit": net_profit,
                "net_margin": round(margin * 100, 1),
                "eps": eps,
                "pe": pe,
                "target_price": target_price,
            }

        return {
            "bear": _calc(revenue_growth_bear, margin_bear, pe_bear),
            "base": _calc(revenue_growth_base, margin_base, pe_base),
            "bull": _calc(revenue_growth_bull, margin_bull, pe_bull),
        }
