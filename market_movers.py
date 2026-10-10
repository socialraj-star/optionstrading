import yfinance as yf
import pandas as pd
import numpy as np
import streamlit as st
from datetime import datetime, timedelta

# Broad universe of actively traded, optionable stocks
SCREEN_UNIVERSE = [
    # Mega-cap tech / Mag 10
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "META", "TSLA", "AVGO", "ORCL", "NFLX",
    # Semis & hardware
    "AMD", "INTC", "QCOM", "TXN", "AMAT", "KLAC", "LRCX", "MU", "SMCI", "ARM",
    "SNDK", "WDC", "STX", "DELL", "HPQ",
    # Software / cloud
    "ADBE", "CRM", "NOW", "INTU", "IBM", "CRWD", "PANW", "DDOG", "SNOW", "NET",
    "ZS", "OKTA", "SHOP", "MELI", "GTLB",
    # High-beta growth
    "PLTR", "COIN", "MSTR", "SOFI", "UBER", "LYFT", "ROKU", "IREN", "CLS",
    "RIVN", "PATH", "AI",
    # Financials
    "JPM", "BAC", "GS", "MS", "C", "WFC", "AXP", "V", "MA", "PYPL", "BLK",
    # Healthcare & biotech
    "UNH", "LLY", "JNJ", "ABBV", "MRK", "PFE", "TMO", "AMGN", "GILD", "ISRG",
    # Consumer
    "WMT", "COST", "HD", "TGT", "MCD", "SBUX", "NKE", "PG", "PEP",
    # Energy
    "XOM", "CVX", "OXY",
    # Industrial
    "GE", "CAT", "DE", "HON", "BA", "RTX", "ETN",
    # Telecom / media
    "DIS", "CMCSA", "T", "VZ",
]


@st.cache_data(ttl=14400, show_spinner=False)
def get_consistent_movers(
    days: int = 14,
    top_n_per_day: int = 10,
    min_appearances: int = 3,
) -> list[dict]:
    """
    For each of the last `days` trading days, find the top `top_n_per_day` stocks
    by absolute % move. Return stocks that appeared that many days >= min_appearances.
    """
    end = datetime.today()
    start = end - timedelta(days=45)  # extra buffer for weekends/holidays

    try:
        raw = yf.download(
            SCREEN_UNIVERSE,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
    except Exception:
        return []

    # Handle both multi-level and single-level columns
    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Close"]
    else:
        close = raw

    # Keep only the last `days` trading days
    close = close.dropna(how="all").iloc[-days:]

    if close.empty or len(close) < 2:
        return []

    # Daily absolute % change
    daily_pct = close.pct_change().abs().iloc[1:]  # drop first NaN row

    # Count appearances in top N per day
    appearance_count: dict[str, int] = {}
    daily_avg_move: dict[str, list[float]] = {}

    for _, row in daily_pct.iterrows():
        row = row.dropna()
        top_today = row.nlargest(top_n_per_day).index.tolist()
        for sym in top_today:
            appearance_count[sym] = appearance_count.get(sym, 0) + 1
            daily_avg_move.setdefault(sym, []).append(float(row[sym]) * 100)

    results = []
    for sym, count in appearance_count.items():
        if count < min_appearances:
            continue
        moves = daily_avg_move.get(sym, [])
        results.append({
            "Symbol": sym,
            "Days in Top 10": count,
            "Avg Daily Move %": round(float(np.mean(moves)), 2) if moves else 0.0,
            "Max Daily Move %": round(float(np.max(moves)), 2) if moves else 0.0,
        })

    return sorted(results, key=lambda x: (x["Days in Top 10"], x["Avg Daily Move %"]), reverse=True)
