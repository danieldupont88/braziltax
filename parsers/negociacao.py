"""
Parser for B3 trade history report (negociacao-*.xlsx).

The file has a single sheet "Negociação" with columns:
  Data do Negócio | Tipo de Movimentação | Mercado | Prazo/Vencimento |
  Instituição | Código de Negociação | Quantidade | Preço | Valor

Fracionário tickers end in 'F' (e.g. BBAS3F, ITSA4F) — they represent
fractional lots of the same underlying asset and are normalised to the
base ticker (BBAS3, ITSA4) for cost-basis tracking.
"""

import os
import glob
import re
import warnings
import pandas as pd


SHEET_NAME = "Negociação"

# Tickers matching these patterns are classified accordingly.
# BDRs end in 34, 32, 33, 35 (level I/II/III) and are listed on B3.
# FIIs end in 11.
# ETFs: IVVB11 is an ETF, but so are BOVA11, SMAL11, etc.
# We use a known-ETF set for disambiguation since 11 is shared with FIIs.
KNOWN_ETFS = {
    "BOVA11", "SMAL11", "IVVB11", "BRAX11", "SPXI11", "HASH11",
    "GOLD11", "DIVO11", "FIND11", "XFIX11", "TRIG11", "AUVP11",
}

_BDR_PATTERN = re.compile(r"^[A-Z]{4}3[2-5]$")
_FII_PATTERN = re.compile(r"^[A-Z]{4}11$")


def _classify(ticker: str) -> str:
    if ticker in KNOWN_ETFS:
        return "ETF"
    if _BDR_PATTERN.match(ticker):
        return "BDR"
    if _FII_PATTERN.match(ticker):
        return "FII"
    return "Ação"


def _normalize_ticker(ticker: str) -> str:
    """Strip trailing 'F' from fracionário tickers."""
    if isinstance(ticker, str) and ticker.endswith("F") and len(ticker) > 2:
        base = ticker[:-1]
        # Only strip if result still looks like a valid B3 ticker (4 letters + digits)
        if re.match(r"^[A-Z]{4}\d+$", base):
            return base
    return ticker


def parse_negociacao(filepath: str) -> pd.DataFrame:
    """
    Parse a single negociacao-*.xlsx file.

    Returns a DataFrame with columns:
      Data | Tipo | Mercado | Instituição | Ticker | Tipo de Ativo |
      Quantidade | Preço | Valor | Fracionário
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = pd.read_excel(filepath, sheet_name=SHEET_NAME)

    df.columns = [
        "Data", "Tipo", "Mercado", "Prazo",
        "Instituição", "Ticker_Raw", "Quantidade", "Preço", "Valor",
    ]

    df = df[df["Data"].notna() & (df["Data"] != "Data do Negócio")].copy()

    df["Data"] = pd.to_datetime(df["Data"], dayfirst=True, errors="coerce")
    df.dropna(subset=["Data"], inplace=True)

    df["Fracionário"] = df["Ticker_Raw"].apply(
        lambda t: isinstance(t, str) and t.endswith("F") and re.match(r"^[A-Z]{4}\d+F$", t) is not None
    )
    df["Ticker"] = df["Ticker_Raw"].apply(_normalize_ticker)
    df["Tipo de Ativo"] = df["Ticker"].apply(_classify)

    df["Quantidade"] = pd.to_numeric(df["Quantidade"], errors="coerce")
    df["Preço"] = pd.to_numeric(df["Preço"], errors="coerce")
    df["Valor"] = pd.to_numeric(df["Valor"], errors="coerce")
    df.dropna(subset=["Quantidade", "Preço"], inplace=True)

    # Sells have positive Quantidade in the file — negate for signed arithmetic
    df.loc[df["Tipo"] == "Venda", "Quantidade"] = -df.loc[df["Tipo"] == "Venda", "Quantidade"]

    df.sort_values("Data", inplace=True)
    df.reset_index(drop=True, inplace=True)

    return df[["Data", "Tipo", "Mercado", "Instituição", "Ticker", "Tipo de Ativo",
               "Quantidade", "Preço", "Valor", "Fracionário"]]


def load_all_negociacao(reports_dir: str) -> pd.DataFrame:
    """
    Load all negociacao-*.xlsx files from reports_dir and concatenate them.
    Deduplicates rows in case date ranges overlap between files.
    """
    pattern = os.path.join(reports_dir, "negociacao-*.xlsx")
    files = sorted(glob.glob(pattern))
    if not files:
        raise FileNotFoundError(
            f"No negociacao reports found matching: {pattern}\n"
            "Place your negociacao-*.xlsx files in that directory."
        )

    frames = []
    for f in files:
        frames.append(parse_negociacao(f))

    df = pd.concat(frames, ignore_index=True)
    df.drop_duplicates(subset=["Data", "Ticker", "Quantidade", "Preço"], inplace=True)
    df.sort_values("Data", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df
