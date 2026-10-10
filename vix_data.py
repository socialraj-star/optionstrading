import ssl_patch  # must be first — fixes SSL cert errors on Windows/Python 3.14
import yfinance as yf
import numpy as np
import streamlit as st


@st.cache_data(ttl=300, show_spinner=False)
def get_vix_context() -> dict:
    try:
        vix_hist = yf.Ticker("^VIX").history(period="6mo")
        if vix_hist.empty:
            return _fallback()

        close = vix_hist["Close"]
        current = float(close.iloc[-1])
        prev_5d = float(close.iloc[-6]) if len(close) > 5 else current
        high_3m = float(close.iloc[-63:].max())
        low_3m = float(close.iloc[-63:].min())
        vix_rank = round((current - low_3m) / (high_3m - low_3m) * 100, 1) if high_3m != low_3m else 50.0

        # VIX3M for term structure
        try:
            vix3m_hist = yf.Ticker("^VIX3M").history(period="5d")
            vix3m = float(vix3m_hist["Close"].iloc[-1]) if not vix3m_hist.empty else None
        except Exception:
            vix3m = None

        if vix3m:
            term_structure = "contango" if current < vix3m else "backwardation"
            term_spread = round(vix3m - current, 2)
        else:
            term_structure = "unknown"
            term_spread = None

        # Regime
        if current < 15:
            regime = "low"
            regime_note = "Low fear — premium thin, be selective on puts"
        elif current < 20:
            regime = "normal"
            regime_note = "Normal — solid premium available"
        elif current < 30:
            regime = "elevated"
            regime_note = "Elevated fear — premium rich, good for put selling"
        else:
            regime = "high"
            regime_note = "High fear / crisis — extreme premium but dangerous assignment risk"

        return {
            "vix": round(current, 2),
            "vix_5d_change": round(current - prev_5d, 2),
            "vix_rank_3m": vix_rank,
            "vix3m": round(vix3m, 2) if vix3m else None,
            "term_structure": term_structure,
            "term_spread": term_spread,
            "regime": regime,
            "regime_note": regime_note,
        }
    except Exception:
        return _fallback()


def _fallback():
    return {
        "vix": None, "vix_5d_change": None, "vix_rank_3m": None,
        "vix3m": None, "term_structure": "unknown", "term_spread": None,
        "regime": "unknown", "regime_note": "VIX data unavailable",
    }
