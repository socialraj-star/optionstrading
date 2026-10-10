import ssl_patch  # must be first — fixes SSL cert errors on Windows/Python 3.14
import yfinance as yf
import pandas as pd
import numpy as np
import streamlit as st
from scipy.stats import norm
from datetime import date
import time
from config import RISK_FREE_RATE


def bs_delta(S: float, K: float, T: float, sigma: float, option_type: str = "put") -> float:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (np.log(S / K) + (RISK_FREE_RATE + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    if option_type == "call":
        return float(norm.cdf(d1))
    return float(norm.cdf(d1) - 1)


def _resolve_price(ticker) -> float | None:
    try:
        fi = ticker.fast_info
        p = getattr(fi, "last_price", None)
        if p and p > 0:
            return float(p)
        p = getattr(fi, "previous_close", None)
        if p and p > 0:
            return float(p)
    except Exception:
        pass
    try:
        hist = ticker.history(period="5d", interval="1d")
        if not hist.empty:
            return float(hist["Close"].dropna().iloc[-1])
    except Exception:
        pass
    return None


@st.cache_data(ttl=900, show_spinner=False)
def get_option_chain(symbol: str, option_type: str = "puts") -> pd.DataFrame | None:
    try:
        ticker = yf.Ticker(symbol)
        price = _resolve_price(ticker)
        if not price:
            return None

        expirations = ticker.options
        if not expirations:
            return None

        today = date.today()
        rows = []

        for exp_str in expirations:
            exp_date = date.fromisoformat(exp_str)
            dte = (exp_date - today).days
            if dte < 1:
                continue

            T = dte / 365.0
            chain = ticker.option_chain(exp_str)
            df = getattr(chain, option_type).copy()

            if df.empty:
                continue

            opt_type = "put" if option_type == "puts" else "call"
            df["expiry"] = exp_str
            df["dte"] = dte
            df["underlying_price"] = price
            df["mid"] = (df["bid"] + df["ask"]) / 2
            df["delta"] = df.apply(
                lambda r: bs_delta(price, r["strike"], T, r["impliedVolatility"], opt_type)
                if r["impliedVolatility"] > 0 else 0.0,
                axis=1,
            )
            df["premium_pct"] = df["mid"] / df["strike"] * 100

            rows.append(df)

        if not rows:
            return None

        return pd.concat(rows, ignore_index=True)

    except Exception:
        return None


# ── Tastytrade IV Rank Integration ────────────────────────────────────────────

def get_iv_rank_for_symbols(symbols: list[str]) -> dict[str, dict]:
    """
    Get IV Rank from Tastytrade if logged in, otherwise return empty.
    Uses session_state cache (5 min TTL) to avoid repeated API calls.
    Returns: {symbol: {iv_rank, iv_pct, iv, iv_5d_change, ...}}
    """
    tt = st.session_state.get("tt_client")
    if not tt:
        return {}

    cache = st.session_state.get("tt_metrics_cache", {})
    now = time.time()

    # Check if we have fresh cached data for all symbols
    missing = [s for s in symbols if s not in cache or cache[s].get("_ts", 0) < now - 300]

    if missing:
        try:
            fresh = tt.get_market_metrics(missing)
            for sym, data in fresh.items():
                cache[sym] = {**data, "_ts": now}
            st.session_state["tt_metrics_cache"] = cache
        except Exception:
            pass

    return {s: {k: v for k, v in cache.get(s, {}).items() if k != "_ts"} for s in symbols}
