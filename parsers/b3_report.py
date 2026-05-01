"""
Parser for B3 annual consolidated report (relatorio-consolidado-anual-YEAR.xlsx).

Each file has five sheets:
  - Posição - Ações   : stock positions at year end
  - Posição - BDR     : BDR positions at year end
  - Posição - ETF     : ETF positions at year end
  - Posição - Fundos  : FII / fund positions at year end
  - Proventos Recebidos : dividends, JCP and FII income received during the year
"""

import os
import glob
import warnings
import pandas as pd


SHEET_ACOES = "Posição - Ações"
SHEET_BDR = "Posição - BDR"
SHEET_ETF = "Posição - ETF"
SHEET_FUNDOS = "Posição - Fundos"
SHEET_PROVENTOS = "Proventos Recebidos"

POSITION_COLS = ["Código de Negociação", "Quantidade", "Preço de Fechamento", "Valor Atualizado"]
PROVENTOS_COLS = ["Produto", "Tipo de Evento", "Valor líquido"]

ASSET_TYPE_MAP = {
    SHEET_ACOES: "Ação",
    SHEET_BDR: "BDR",
    SHEET_ETF: "ETF",
    SHEET_FUNDOS: "FII",
}


def _read_position_sheet(wb, sheet_name: str, asset_type: str) -> pd.DataFrame:
    if sheet_name not in wb.sheetnames:
        return pd.DataFrame()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = pd.read_excel(wb, sheet_name=sheet_name)

    # Drop trailing summary rows (empty Produto / ticker column)
    ticker_col = "Código de Negociação"
    if ticker_col not in df.columns:
        return pd.DataFrame()

    df = df[df[ticker_col].notna() & (df[ticker_col] != "")].copy()

    # Keep only the columns we need
    available = [c for c in POSITION_COLS if c in df.columns]
    df = df[available].copy()
    df["Tipo"] = asset_type

    df["Quantidade"] = pd.to_numeric(df["Quantidade"], errors="coerce")
    df["Preço de Fechamento"] = pd.to_numeric(df.get("Preço de Fechamento"), errors="coerce")
    df["Valor Atualizado"] = pd.to_numeric(df.get("Valor Atualizado"), errors="coerce")
    df.dropna(subset=["Quantidade"], inplace=True)

    return df


def _read_proventos_sheet(wb, year: int) -> pd.DataFrame:
    if SHEET_PROVENTOS not in wb.sheetnames:
        return pd.DataFrame()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = pd.read_excel(wb, sheet_name=SHEET_PROVENTOS)

    df = df[PROVENTOS_COLS].copy()
    df.columns = ["Ticker", "Tipo de Evento", "Valor líquido"]

    # Drop summary / empty rows
    df = df[df["Ticker"].notna() & (df["Ticker"] != "")].copy()
    df["Valor líquido"] = pd.to_numeric(df["Valor líquido"], errors="coerce")
    df.dropna(subset=["Valor líquido"], inplace=True)

    df["Ano"] = year
    return df


def parse_report(filepath: str) -> dict[str, pd.DataFrame]:
    """
    Parse a single B3 annual report file.

    Returns a dict with keys:
      - "posicoes": DataFrame of all asset positions
      - "proventos": DataFrame of all income events (dividends, JCP, FII rendimentos)
    """
    year = _extract_year(filepath)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = pd.ExcelFile(filepath)

    # Expose a sheetnames attribute for compatibility
    wb.sheetnames = wb.sheet_names

    positions = []
    for sheet, asset_type in ASSET_TYPE_MAP.items():
        df = _read_position_sheet(wb, sheet, asset_type)
        if not df.empty:
            df["Ano"] = year
            positions.append(df)

    df_positions = pd.concat(positions, ignore_index=True) if positions else pd.DataFrame()
    df_proventos = _read_proventos_sheet(wb, year)

    return {"posicoes": df_positions, "proventos": df_proventos}


def load_all_reports(reports_dir: str) -> dict[str, pd.DataFrame]:
    """
    Load all relatorio-consolidado-anual-*.xlsx files from the given directory.

    Returns combined "posicoes" and "proventos" DataFrames across all years.
    """
    pattern = os.path.join(reports_dir, "relatorio-consolidado-anual-*.xlsx")
    files = sorted(glob.glob(pattern))
    if not files:
        raise FileNotFoundError(
            f"No B3 annual reports found matching: {pattern}\n"
            "Place your relatorio-consolidado-anual-YEAR.xlsx files in that directory."
        )

    all_positions = []
    all_proventos = []

    for filepath in files:
        result = parse_report(filepath)
        if not result["posicoes"].empty:
            all_positions.append(result["posicoes"])
        if not result["proventos"].empty:
            all_proventos.append(result["proventos"])

    df_positions = pd.concat(all_positions, ignore_index=True) if all_positions else pd.DataFrame()
    df_proventos = pd.concat(all_proventos, ignore_index=True) if all_proventos else pd.DataFrame()

    return {"posicoes": df_positions, "proventos": df_proventos}


def _extract_year(filepath: str) -> int:
    basename = os.path.basename(filepath)
    # expects relatorio-consolidado-anual-YYYY.xlsx
    parts = basename.replace(".xlsx", "").split("-")
    try:
        return int(parts[-1])
    except (ValueError, IndexError):
        return 0
