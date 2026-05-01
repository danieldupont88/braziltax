# brasiltax

Brazilian IRPF tax calculator for B3 investors. Reads your trade history from B3's
**Negociação** report and computes:

- Realised gains and losses per month, per asset type
- Monthly DARF amounts due
- Current positions with **preço médio ponderado** (weighted average cost basis)
- Yearly summary for IRPF declaration

> **Disclaimer:** Results are estimates and do not replace a qualified accountant.
> Brokerage fees and emoluments (which reduce taxable gains) are not included.
> Always audit before filing.

---

## Project structure

```
brasiltax/
├── main.py                  # Entry point
├── events.toml              # Corporate events config (splits, BDR ratio changes)
├── parsers/
│   ├── negociacao.py        # Parses negociacao-*.xlsx trade reports
│   └── events.py            # Loads and applies corporate events to trade history
├── tax/
│   └── calculator.py        # Preço médio ponderado + Brazilian tax rules
├── reports/                 # Drop your .xlsx files here (gitignored)
└── pyproject.toml
```

---

## Requirements

- Python 3.9+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

Dependencies (declared in `pyproject.toml`):

| Package    | Purpose                                        |
|------------|------------------------------------------------|
| `pandas`   | Data manipulation                              |
| `openpyxl` | Reading `.xlsx` files                          |
| `rich`     | Terminal output formatting                     |
| `tomli`    | TOML parsing on Python < 3.11 (auto-installed) |

---

## Setup

### With uv (recommended)

```bash
cd brasiltax
uv sync
```

### With pip

```bash
cd brasiltax
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install pandas openpyxl rich "tomli; python_version<'3.11'"
```

---

## How to run

1. Place your `negociacao-*.xlsx` file(s) in the `reports/` directory.
2. Run:

```bash
# With uv
uv run python main.py

# With pip / activated venv
python main.py

# Custom reports directory
python main.py /path/to/your/reports
```

The default reports directory is `./reports`.

---

## Input file format

### negociacao-*.xlsx

This is the **trade history report** exported from [B3's investor portal](https://www.b3.com.br/pt_br/para-voce/educacional/como-e-a-b3/portal-do-investidor.htm) (Canal do Investidor / Extrato de Negociação).

**How to download:**

1. Log in to [investidor.b3.com.br](https://investidor.b3.com.br)
2. Go to **Extratos e Informes → Negociação**
3. Select the date range and export as `.xlsx`
4. The file will be named `negociacao-YYYY-MM-DD-HH-MM-SS.xlsx`

**Expected sheet:** `Negociação`

**Expected columns:**

| Column                 | Type     | Example              | Notes                                      |
|------------------------|----------|----------------------|--------------------------------------------|
| `Data do Negócio`      | date     | `15/04/2026`         | Format `dd/mm/yyyy`                        |
| `Tipo de Movimentação` | string   | `Compra` / `Venda`   | Only these two values are processed        |
| `Mercado`              | string   | `Mercado à Vista`    | `Mercado Fracionário` is also handled      |
| `Prazo/Vencimento`     | string   | `-`                  | Ignored                                    |
| `Instituição`          | string   | `XP INVESTIMENTOS…`  | Informational only                         |
| `Código de Negociação` | string   | `BBAS3` / `BBAS3F`   | Fracionário tickers end in `F` (normalised)|
| `Quantidade`           | integer  | `30`                 | Always positive in the raw file            |
| `Preço`                | decimal  | `23.60`              | Price per unit in BRL                      |
| `Valor`                | decimal  | `708.00`             | Total value = Quantidade × Preço           |

**Example rows:**

```
Data do Negócio | Tipo de Movimentação | Mercado              | ... | Código | Qtd | Preço  | Valor
15/04/2026      | Compra               | Mercado à Vista      | ... | HGLG11 | 2   | 157.23 | 314.46
01/04/2026      | Compra               | Mercado Fracionário  | ... | BBAS3F | 30  | 23.60  | 708.00
09/01/2026      | Venda                | Mercado à Vista      | ... | AUVP11 | 77  | 115.83 | 8918.91
```

**Multiple files:** You can place multiple `negociacao-*.xlsx` files (e.g. one per year)
in `reports/`. They will be merged and deduplicated automatically.

---

## Ticker classification

Tickers are automatically classified into asset types based on their format:

| Asset type | Rule                                   | Examples                        |
|------------|----------------------------------------|---------------------------------|
| BDR        | Ends in `34`, `32`, `33`, or `35`      | `AAPL34`, `MSFT34`, `NVDC34`    |
| FII        | Ends in `11` and not in the ETF list   | `HGLG11`, `KNRI11`, `XPML11`   |
| ETF        | Ends in `11` and in the known-ETF list | `IVVB11`, `BOVA11`, `AUVP11`   |
| Ação       | Everything else                        | `BBAS3`, `WEGE3`, `ITSA4`      |

Fracionário tickers (`BBAS3F`, `ITSA4F`) are normalised to their base ticker
(`BBAS3`, `ITSA4`) before classification and cost-basis tracking.

To add a missing ETF to the classification list, edit the `KNOWN_ETFS` set in
`parsers/negociacao.py`.

---

## Corporate events (splits and BDR ratio changes)

Stock splits and BDR ratio adjustments are declared in `events.toml`. When an
event is present, all buys for that ticker **before** the event date are
retroactively adjusted — quantity is multiplied by the ratio and price is
divided by it — so the total acquisition cost is preserved and cost basis
remains correct.

### events.toml format

```toml
[[events]]
ticker = "NVDC34"
date   = "2024-06-10"
ratio  = 10.0
note   = "NVIDIA 10:1 stock split"

[[events]]
ticker = "NVDC34"
date   = "2024-06-10"
ratio  = 3.5
note   = "B3 BDR ratio adjustment for NVDC34 (3.5x)"
```

| Field    | Description                                                                 |
|----------|-----------------------------------------------------------------------------|
| `ticker` | Normalised ticker (no trailing `F`)                                         |
| `date`   | Ex-date of the event (`YYYY-MM-DD`)                                         |
| `ratio`  | Multiplier: `> 1` = split (more shares), `< 1` = reverse split (grupamento)|
| `note`   | Optional description                                                        |

Multiple events for the same ticker are applied in chronological order.

**Where to find corporate events:**  
B3 Eventos Corporativos: `https://sistemaswebb3-listados.b3.com.br/corporateEventsProxy`  
Or search `<TICKER> desdobramento` / `<TICKER> grupamento` on Status Invest or Fundamentus.

### Why this matters

Without split adjustments, historical buys appear to have a zero or inflated
cost basis, producing incorrect gains/losses. Example: 6 NVDC34 BDRs bought in
2021 became 210 after a combined 35x adjustment (NVIDIA 10:1 stock split ×
3.5x B3 BDR ratio change). Without the adjustment, the Nov 2025 sell appeared
as a R$7,801 loss; with it, the correct R$4,173 gain and R$626 DARF are shown.

---

## Tax rules applied

| Asset type | Rate | Monthly sell exemption             |
|------------|------|------------------------------------|
| Ação       | 15%  | Exempt if total month sells ≤ R$20,000 |
| FII        | 20%  | None                               |
| BDR        | 15%  | None                               |
| ETF        | 15%  | None                               |

**Cost basis method:** preço médio ponderado (weighted average), as required by
Receita Federal.

**Loss carry-forward:** Losses in a given asset type are accumulated and offset
against future gains of the same type. The "Prejuízo Abatido" column shows how
much accumulated loss was applied in each month.

**DARF code:** 6015 for all asset types (renda variável — operações comuns).

**Not covered:**
- Day-trade (20% rate) — requires intraday timestamps not present in this report
- Brokerage fees / emoluments — reduce taxable gain but are not in the input file
- Isenção de 20k for gains on months where an asset was partially acquired the same day as the sale (edge case)

---

## Output sections

### 1. Resumo Anual

Yearly totals: total sold, net gain/loss, and total DARF per asset type.

### 2. Ganhos e Impostos por Mês

Month-by-month breakdown with:
- Total sold in the month
- Gross gain or loss
- Accumulated loss absorbed (Prejuízo Abatido)
- Taxable gain after exemptions and loss carry-forward
- DARF amount due
- Status: `Isento` / `Prejuízo` / `DARF R$ X` / `OK`

### 3. Posições Atuais

All open positions at the time of the last trade in the input files, showing:
- Ticker and asset type
- Current quantity
- Weighted average cost (preço médio ponderado)
- Total acquisition cost — use this value for IRPF **Bens e Direitos**

### 4. Próximos Passos

Step-by-step instructions for filing the current IRPF declaration year, including
which IRPF fields to fill and which DARF codes to use.

---

## IRPF declaration guide

### Bens e Direitos (Grupo 03 — Participações Societárias)

For each position in the **Posições Atuais** table:

- **Grupo / Código:** `03 – 01` (Ações) / `03 – 03` (FII) / `03 – 04` (BDR/ETF)
- **Discriminação:** `<Qtd> ações/cotas de <Nome da empresa>, código <TICKER>, custodiado em <Corretora>`
- **Situação em 31/12/ano-base:** Use the **Custo Total (R$)** from the positions table (acquisition cost, not market value)

### Renda Variável (Ganhos de Capital)

- Use the **Ganhos e Impostos por Mês** table filtered to the declaration year
- For each month with `Imposto Devido > 0`, a DARF (código 6015) must have been paid by the last business day of the following month
- Declare cumulative gains and losses in the **Renda Variável** section of the IRPF program

### Prejuízos a compensar

Accumulated losses not yet offset are tracked internally and will be applied
automatically in subsequent months. If you have losses from prior years not yet
absorbed, they should be carried into the next year's run — ensure your input
file covers all historical trades from the beginning.
