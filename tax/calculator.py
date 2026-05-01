"""
Brazilian IRPF tax calculator — trade-level, based on negociacao reports.

Rules applied (Receita Federal):

  Asset type  | Rate  | Monthly sell exemption  | Loss pool
  ------------|-------|-------------------------|--------------------
  Ação        | 15%   | Exempt if sells ≤ R$20k | Renda Variável (shared)
  BDR         | 15%   | None                    | Renda Variável (shared)
  ETF (equity)| 15%   | None                    | Renda Variável (shared)
  FII         | 20%   | None                    | FII (isolated)

Ação, BDR and ETF losses are cross-compensable (same DARF code 6015).
FII losses can only offset FII gains.

Cost basis method: preço médio ponderado (weighted average), per Receita Federal.
Losses are carried forward and offset future gains within the same pool.
"""

from __future__ import annotations

from dataclasses import dataclass
import pandas as pd

MONTHLY_EXEMPTION_ACOES = 20_000.0

TAX_RATES: dict[str, float] = {
    "Ação": 0.15,
    "FII": 0.20,
    "BDR": 0.15,
    "ETF": 0.15,
}

# Assets that share the same loss/gain compensation pool (DARF 6015)
RENDA_VARIAVEL_POOL = {"Ação", "BDR", "ETF"}
FII_POOL = {"FII"}


def _loss_pool_for(asset_type: str) -> str:
    return "rv" if asset_type in RENDA_VARIAVEL_POOL else "fii"


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
class TickerSellRecord:
    year: int
    month: int
    ticker: str
    asset_type: str
    qty_sold: float = 0.0
    avg_cost: float = 0.0
    sell_price: float = 0.0
    sell_value: float = 0.0
    gross_gain: float = 0.0
    classification: str = ""


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


def compute_gains(df: pd.DataFrame) -> tuple[list[MonthlyResult], dict[str, TickerState], list[TickerSellRecord]]:
    """
    Process all trades chronologically and return:
      - list of MonthlyResult (one per year/month/asset_type with sell activity)
      - final dict of TickerState (current positions with avg cost)
      - list of TickerSellRecord (one per sell row, for per-ticker reporting)

    Loss pools:
      "rv"  — Ação + BDR + ETF (cross-compensable, DARF 6015)
      "fii" — FII only
    """
    states: dict[str, TickerState] = {}
    loss_pool: dict[str, float] = {"rv": 0.0, "fii": 0.0}

    df = df.copy()
    df["YearMonth"] = df["Data"].dt.to_period("M")
    # Within each day, process buys before sells to avoid zero-cost-basis on same-day buy+sell
    df["_sort_key"] = (df["Tipo"] == "Venda").astype(int)
    df.sort_values(["Data", "_sort_key"], inplace=True)
    df.drop(columns=["_sort_key"], inplace=True)

    results: list[MonthlyResult] = []
    ticker_records: list[TickerSellRecord] = []

    for period, month_df in df.groupby("YearMonth"):
        month_stats: dict[str, dict] = {}
        month_ticker_records: list[TickerSellRecord] = []

        # Pass 1: accumulate buys/sells, collect raw per-ticker records
        for _, row in month_df.iterrows():
            ticker = row["Ticker"]
            asset_type = row["Tipo de Ativo"]
            qty = abs(row["Quantidade"])
            price = row["Preço"]
            is_sell = row["Tipo"] == "Venda"

            if ticker not in states:
                states[ticker] = TickerState(ticker=ticker, asset_type=asset_type)

            if is_sell:
                avg_cost_at_sell = states[ticker].avg_cost
                gain = states[ticker].sell(qty, price)
                sell_value = qty * price
                if asset_type not in month_stats:
                    month_stats[asset_type] = {"sell_value": 0.0, "gross_gain": 0.0}
                month_stats[asset_type]["sell_value"] += sell_value
                month_stats[asset_type]["gross_gain"] += gain
                month_ticker_records.append(TickerSellRecord(
                    year=period.year,
                    month=period.month,
                    ticker=ticker,
                    asset_type=asset_type,
                    qty_sold=qty,
                    avg_cost=avg_cost_at_sell,
                    sell_price=price,
                    sell_value=sell_value,
                    gross_gain=gain,
                ))
            else:
                states[ticker].buy(qty, price)

        # Only emit MonthlyResult rows where there were actual sells
        if not month_stats:
            continue
        #
        # RV pool (Ação + BDR + ETF):
        #   1. Ação sells ≤ R$20k are exempt — their losses still enter the RV net
        #      but their gains do NOT contribute taxable income.
        #   2. Net RV taxable gain = sum of non-exempt gains + all losses in the pool.
        #   3. Carry-forward losses are absorbed against the net RV taxable gain.
        #   4. Remaining taxable gain is split proportionally to each RV asset type
        #      by its share of the gross non-exempt gains.
        #
        # FII pool: isolated, same logic without the exemption step.

        # Separate asset types by pool
        rv_types = {at: s for at, s in month_stats.items() if _loss_pool_for(at) == "rv"}
        fii_types = {at: s for at, s in month_stats.items() if _loss_pool_for(at) == "fii"}

        month_classification: dict[str, str] = {}

        def _process_pool(
            pool_stats: dict[str, dict],
            pool_key: str,
            has_exemption: bool,
        ) -> None:
            if not pool_stats:
                return

            # Step 1: determine exempt status and compute pool net taxable gain
            exempt_map: dict[str, bool] = {}
            for at, stats in pool_stats.items():
                is_exempt = has_exemption and at == "Ação" and stats["sell_value"] <= MONTHLY_EXEMPTION_ACOES
                exempt_map[at] = is_exempt

            # Net taxable gain for the pool:
            #   losses always count; exempt gains do NOT (already not taxed)
            pool_net_taxable = sum(
                s["gross_gain"] if (s["gross_gain"] < 0 or not exempt_map[at]) else 0.0
                for at, s in pool_stats.items()
            )

            # Step 2: absorb carry-forward losses against pool net taxable gain
            absorbed_total = 0.0
            if pool_net_taxable > 0 and loss_pool[pool_key] > 0:
                absorbed_total = min(loss_pool[pool_key], pool_net_taxable)
                loss_pool[pool_key] -= absorbed_total
            elif pool_net_taxable < 0:
                loss_pool[pool_key] += abs(pool_net_taxable)

            net_after_cf = max(pool_net_taxable - absorbed_total, 0.0)

            # Step 3: distribute taxable gain proportionally to non-exempt positive contributors
            positive_gains = {
                at: s["gross_gain"]
                for at, s in pool_stats.items()
                if s["gross_gain"] > 0 and not exempt_map[at]
            }
            total_positive = sum(positive_gains.values())

            for at, stats in pool_stats.items():
                gross_gain = stats["gross_gain"]
                sell_value = stats["sell_value"]
                exempt = exempt_map[at]
                rate = TAX_RATES[at]

                if exempt:
                    if gross_gain < 0:
                        # Exempt loss — already counted in pool_net_taxable, pool updated above
                        pass
                    month_classification[at] = "Isento"
                    results.append(MonthlyResult(
                        year=period.year, month=period.month, asset_type=at,
                        total_sell_value=sell_value, gross_gain=gross_gain,
                        taxable_gain=0.0, tax_due=0.0, exempt=True,
                        loss_carried_forward=0.0,
                    ))
                    continue

                if gross_gain < 0:
                    month_classification[at] = "Prejuízo"
                    results.append(MonthlyResult(
                        year=period.year, month=period.month, asset_type=at,
                        total_sell_value=sell_value, gross_gain=gross_gain,
                        taxable_gain=0.0, tax_due=0.0, exempt=False,
                        loss_carried_forward=0.0,
                    ))
                    continue

                # Positive non-exempt gain: attribute share of net_after_cf
                share = (gross_gain / total_positive) if total_positive > 0 else 0.0
                taxable_gain = net_after_cf * share
                absorbed_for_type = gross_gain - taxable_gain
                tax_due = taxable_gain * rate

                if taxable_gain <= 0:
                    classification = "Zerado por prejuízo anterior"
                else:
                    classification = "Ganho tributável"

                month_classification[at] = classification
                results.append(MonthlyResult(
                    year=period.year, month=period.month, asset_type=at,
                    total_sell_value=sell_value, gross_gain=gross_gain,
                    taxable_gain=taxable_gain, tax_due=tax_due, exempt=False,
                    loss_carried_forward=absorbed_for_type,
                ))

        _process_pool(rv_types, "rv", has_exemption=True)
        _process_pool(fii_types, "fii", has_exemption=False)

        # Annotate ticker records with the month's classification for their asset type
        for rec in month_ticker_records:
            rec.classification = month_classification.get(rec.asset_type, "")
            ticker_records.append(rec)

    return results, states, ticker_records


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


def ticker_records_to_df(records: list[TickerSellRecord]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    return pd.DataFrame([
        {
            "Ano": r.year,
            "Mês": r.month,
            "Ticker": r.ticker,
            "Tipo de Ativo": r.asset_type,
            "Qtd Vendida": r.qty_sold,
            "Preço Médio Custo (R$)": r.avg_cost,
            "Preço de Venda (R$)": r.sell_price,
            "Total Vendido (R$)": r.sell_value,
            "Ganho/Perda Bruto (R$)": r.gross_gain,
            "Classificação": r.classification,
        }
        for r in records
    ])


def export_yearly_reports(records: list[TickerSellRecord], output_dir: str = "output") -> list[str]:
    """
    Write one Excel file per year to output_dir.
    Returns list of file paths written.
    """
    import os
    os.makedirs(output_dir, exist_ok=True)

    df = ticker_records_to_df(records)
    if df.empty:
        return []

    written = []
    for year, year_df in df.groupby("Ano"):
        path = os.path.join(output_dir, f"brasiltax-{year}.xlsx")
        year_df = year_df.drop(columns=["Ano"]).reset_index(drop=True)
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            year_df.to_excel(writer, index=False, sheet_name="Operações de Venda")
            # Auto-fit column widths
            ws = writer.sheets["Operações de Venda"]
            for col in ws.columns:
                max_len = max(len(str(cell.value)) if cell.value is not None else 0 for cell in col)
                ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 40)
        written.append(path)

    return written


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
