"""
Brazilian IRPF tax calculator — trade-level, based on negociacao reports.

Rules applied (Receita Federal):

  Asset type  | Rate  | Monthly sell exemption
  ------------|-------|------------------------
  Ação        | 15%   | Exempt if total month sells ≤ R$20,000
  FII         | 20%   | None
  BDR         | 15%   | None
  ETF (equity)| 15%   | None

Cost basis method: preço médio ponderado (weighted average), per Receita Federal.
Losses can be carried forward within the same asset type and offset future gains.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import pandas as pd

MONTHLY_EXEMPTION_ACOES = 20_000.0

TAX_RATES: dict[str, float] = {
    "Ação": 0.15,
    "FII": 0.20,
    "BDR": 0.15,
    "ETF": 0.15,
}


@dataclass
class TickerState:
    ticker: str
    asset_type: str
    qty: float = 0.0
    avg_cost: float = 0.0  # preço médio ponderado in R$

    def buy(self, qty: float, price: float) -> None:
        total_cost = self.avg_cost * self.qty + price * qty
        self.qty += qty
        self.avg_cost = total_cost / self.qty if self.qty else 0.0

    def sell(self, qty: float, price: float) -> float:
        """Return realised gain (positive) or loss (negative) in R$."""
        gain = qty * (price - self.avg_cost)
        self.qty -= qty
        if self.qty <= 0:
            self.qty = 0.0
            self.avg_cost = 0.0
        return gain


@dataclass
class MonthlyResult:
    year: int
    month: int
    asset_type: str
    total_sell_value: float = 0.0
    gross_gain: float = 0.0
    taxable_gain: float = 0.0
    tax_due: float = 0.0
    exempt: bool = False
    loss_carried_forward: float = 0.0   # loss absorbed from prior months


def compute_gains(df: pd.DataFrame) -> tuple[list[MonthlyResult], dict[str, TickerState]]:
    """
    Process all trades chronologically and return:
      - list of MonthlyResult (one per year/month/asset_type combination with activity)
      - final dict of TickerState (current positions with avg cost)

    Loss carry-forward is tracked per asset type.
    """
    states: dict[str, TickerState] = {}
    # Accumulated losses per asset type (positive number = loss owed to investor)
    loss_pool: dict[str, float] = {t: 0.0 for t in TAX_RATES}

    df = df.copy()
    df["YearMonth"] = df["Data"].dt.to_period("M")
    # Within each day, process buys before sells to avoid zero-cost-basis on same-day buy+sell
    df["_sort_key"] = (df["Tipo"] == "Venda").astype(int)
    df.sort_values(["Data", "_sort_key"], inplace=True)
    df.drop(columns=["_sort_key"], inplace=True)

    results: list[MonthlyResult] = []

    for period, month_df in df.groupby("YearMonth"):
        # Collect per-asset-type stats for this month
        month_stats: dict[str, dict] = {}

        for _, row in month_df.iterrows():
            ticker = row["Ticker"]
            asset_type = row["Tipo de Ativo"]
            qty = abs(row["Quantidade"])
            price = row["Preço"]
            is_sell = row["Tipo"] == "Venda"

            if ticker not in states:
                states[ticker] = TickerState(ticker=ticker, asset_type=asset_type)

            if asset_type not in month_stats:
                month_stats[asset_type] = {"sell_value": 0.0, "gross_gain": 0.0}

            if is_sell:
                gain = states[ticker].sell(qty, price)
                month_stats[asset_type]["sell_value"] += qty * price
                month_stats[asset_type]["gross_gain"] += gain
            else:
                states[ticker].buy(qty, price)

        for asset_type, stats in month_stats.items():
            sell_value = stats["sell_value"]
            gross_gain = stats["gross_gain"]

            if sell_value == 0.0:
                continue

            rate = TAX_RATES[asset_type]
            exempt = False
            taxable_gain = gross_gain
            absorbed_loss = 0.0

            # Apply loss carry-forward
            if gross_gain > 0 and loss_pool[asset_type] > 0:
                absorbed = min(loss_pool[asset_type], gross_gain)
                taxable_gain = gross_gain - absorbed
                loss_pool[asset_type] -= absorbed
                absorbed_loss = absorbed

            # Accumulate new losses
            if gross_gain < 0:
                loss_pool[asset_type] += abs(gross_gain)
                taxable_gain = 0.0

            # Monthly R$20k exemption for Ações
            if asset_type == "Ação" and sell_value <= MONTHLY_EXEMPTION_ACOES:
                exempt = True
                taxable_gain = 0.0

            tax_due = taxable_gain * rate if taxable_gain > 0 else 0.0

            results.append(MonthlyResult(
                year=period.year,
                month=period.month,
                asset_type=asset_type,
                total_sell_value=sell_value,
                gross_gain=gross_gain,
                taxable_gain=taxable_gain,
                tax_due=tax_due,
                exempt=exempt,
                loss_carried_forward=absorbed_loss,
            ))

    return results, states


def results_to_df(results: list[MonthlyResult]) -> pd.DataFrame:
    if not results:
        return pd.DataFrame()
    rows = [
        {
            "Ano": r.year,
            "Mês": r.month,
            "Tipo de Ativo": r.asset_type,
            "Total Vendido (R$)": r.total_sell_value,
            "Ganho Bruto (R$)": r.gross_gain,
            "Prejuízo Abatido (R$)": r.loss_carried_forward,
            "Ganho Tributável (R$)": r.taxable_gain,
            "Imposto Devido (R$)": r.tax_due,
            "Isento": r.exempt,
        }
        for r in results
    ]
    return pd.DataFrame(rows)


def positions_to_df(states: dict[str, TickerState]) -> pd.DataFrame:
    if not states:
        return pd.DataFrame()
    rows = [
        {
            "Ticker": s.ticker,
            "Tipo de Ativo": s.asset_type,
            "Quantidade": s.qty,
            "Preço Médio (R$)": s.avg_cost,
            "Custo Total (R$)": s.qty * s.avg_cost,
        }
        for s in sorted(states.values(), key=lambda x: (x.asset_type, x.ticker))
        if s.qty > 0
    ]
    return pd.DataFrame(rows)
