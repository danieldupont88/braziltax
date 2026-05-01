"""
Loader for corporate events (splits, BDR ratio changes) from events.toml.

Events are applied to the trade history before cost-basis calculation:
- Buys that occurred BEFORE the event date have their quantity multiplied by
  the ratio and their price divided by the ratio (cost basis is preserved).
- Sells and buys AFTER the event date are already in post-event units and
  are left unchanged.
"""

import os
import sys
from dataclasses import dataclass

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # pip install tomli
import pandas as pd


@dataclass
class CorporateEvent:
    ticker: str
    date: pd.Timestamp
    ratio: float
    note: str = ""


def load_events(config_path: str = "events.toml") -> list[CorporateEvent]:
    if not os.path.exists(config_path):
        return []

    with open(config_path, "rb") as f:
        data = tomllib.load(f)

    events = []
    for e in data.get("events", []):
        events.append(CorporateEvent(
            ticker=e["ticker"],
            date=pd.Timestamp(e["date"]),
            ratio=float(e["ratio"]),
            note=e.get("note", ""),
        ))

    # Sort by ticker then date so multiple events on same ticker apply in order
    events.sort(key=lambda e: (e.ticker, e.date))
    return events


def apply_events(df: pd.DataFrame, events: list[CorporateEvent]) -> pd.DataFrame:
    """
    Adjust historical trade quantities and prices to reflect corporate events.

    For each event, trades in that ticker with Date < event.date have:
      Quantidade *= ratio
      Preço      /= ratio
      Valor       = Quantidade * Preço  (recalculated)

    This preserves total cost basis while putting old trades in post-event units.
    """
    if not events or df.empty:
        return df

    df = df.copy()

    for event in events:
        mask = (df["Ticker"] == event.ticker) & (df["Data"] < event.date)
        if not mask.any():
            continue

        df.loc[mask, "Quantidade"] = df.loc[mask, "Quantidade"] * event.ratio
        df.loc[mask, "Preço"] = df.loc[mask, "Preço"] / event.ratio
        df.loc[mask, "Valor"] = df.loc[mask, "Quantidade"] * df.loc[mask, "Preço"]

    return df
