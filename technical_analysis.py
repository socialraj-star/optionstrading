import ssl_patch  # must be first — fixes SSL cert errors on Windows/Python 3.14
import yfinance as yf
import pandas as pd
import numpy as np
import streamlit as st
from datetime import date
from config import RSI_PERIOD, MACD_FAST, MACD_SLOW, MACD_SIGNAL_PERIOD, BB_PERIOD, BB_STD, ABOVE_MA_PERIOD


@st.cache_data(ttl=300, show_spinner=False)
def get_technicals(symbol: str) -> dict | None:
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1y", interval="1d")
        if len(hist) < 60:
            return None

        close = hist["Close"]
        high = hist["High"]

        rsi = _rsi(close)
        macd_line, signal_line, macd_hist = _macd(close)
        bb_upper, bb_mid, bb_lower = _bollinger(close)

        price = float(close.iloc[-1])
        ma50 = float(close.rolling(ABOVE_MA_PERIOD).mean().iloc[-1])
        ma200 = float(close.rolling(200).mean().iloc[-1])
        high_20d = float(high.rolling(20).max().iloc[-1])

        hv_rank = _hv_rank(close)
        weekly_rsi = _weekly_rsi(ticker)
        earnings_info = _get_earnings(ticker)
        pe = _get_pe(ticker)

        return {
            "symbol": symbol,
            "price": price,
            "rsi": round(float(rsi.iloc[-1]), 1),
            "macd": round(float(macd_line.iloc[-1]), 3),
            "macd_signal": round(float(signal_line.iloc[-1]), 3),
            "macd_hist": round(float(macd_hist.iloc[-1]), 3),
            "macd_crossover": _detect_crossover(macd_hist),
            "bb_upper": round(float(bb_upper.iloc[-1]), 2),
            "bb_mid": round(float(bb_mid.iloc[-1]), 2),
            "bb_lower": round(float(bb_lower.iloc[-1]), 2),
            "bb_pct": round(
                (price - float(bb_lower.iloc[-1]))
                / max(float(bb_upper.iloc[-1]) - float(bb_lower.iloc[-1]), 0.01)
                * 100,
                1,
            ),
            "price_below_bb_mid": price < float(bb_mid.iloc[-1]),
            "above_50ma": price > ma50,
            "above_200ma": price > ma200,
            "ma50": round(ma50, 2),
            "ma200": round(ma200, 2),
            "pullback_pct": round((high_20d - price) / high_20d * 100, 1),
            "hv_rank": hv_rank,
            "weekly_rsi": weekly_rsi,
            "earnings_date": earnings_info.get("date"),
            "days_to_earnings": earnings_info.get("days"),
            "pe": pe,
            "hist": hist,
        }
    except Exception:
        return None


def _hv_rank(close: pd.Series) -> float | None:
    """Historical Volatility Rank (0–100) — proxy for IV Rank.
    Answers: is current volatility high or low vs the past year?
    """
    try:
        log_ret = np.log(close / close.shift(1)).dropna()
        hv30 = log_ret.rolling(30).std() * np.sqrt(252) * 100
        hv30 = hv30.dropna()
        if len(hv30) < 50:
            return None
        window = hv30.iloc[-252:] if len(hv30) >= 252 else hv30
        lo, hi = float(window.min()), float(window.max())
        if hi == lo:
            return 50.0
        return round((float(hv30.iloc[-1]) - lo) / (hi - lo) * 100, 1)
    except Exception:
        return None


def _weekly_rsi(ticker) -> float | None:
    """RSI on weekly chart — confirms higher-timeframe momentum."""
    try:
        weekly = ticker.history(period="2y", interval="1wk")
        if len(weekly) < 20:
            return None
        rsi = _rsi(weekly["Close"])
        return round(float(rsi.iloc[-1]), 1)
    except Exception:
        return None


def _get_earnings(ticker) -> dict:
    """Return next earnings date and days until it."""
    try:
        cal = ticker.calendar
        if cal is None:
            return {}
        # yfinance returns calendar as dict or DataFrame depending on version
        if isinstance(cal, dict):
            earnings_raw = cal.get("Earnings Date", [])
            if isinstance(earnings_raw, list) and earnings_raw:
                ed = pd.Timestamp(earnings_raw[0]).date()
            else:
                return {}
        elif hasattr(cal, "loc"):
            try:
                ed = cal.loc["Earnings Date"].iloc[0]
                ed = pd.Timestamp(ed).date()
            except Exception:
                return {}
        else:
            return {}

        today = date.today()
        days = (ed - today).days
        if days < -5:   # already passed
            return {}
        return {"date": ed.isoformat(), "days": max(0, days)}
    except Exception:
        return {}


def _get_pe(ticker) -> float | None:
    try:
        return ticker.info.get("trailingPE")
    except Exception:
        return None


def _rsi(close: pd.Series) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(com=RSI_PERIOD - 1, min_periods=RSI_PERIOD).mean()
    loss = (-delta.clip(upper=0)).ewm(com=RSI_PERIOD - 1, min_periods=RSI_PERIOD).mean()
    return 100 - 100 / (1 + gain / loss)


def _macd(close: pd.Series):
    ema_fast = close.ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = close.ewm(span=MACD_SLOW, adjust=False).mean()
    line = ema_fast - ema_slow
    signal = line.ewm(span=MACD_SIGNAL_PERIOD, adjust=False).mean()
    return line, signal, line - signal


def _bollinger(close: pd.Series):
    mid = close.rolling(BB_PERIOD).mean()
    std = close.rolling(BB_PERIOD).std()
    return mid + BB_STD * std, mid, mid - BB_STD * std


def _detect_crossover(hist: pd.Series) -> str:
    if len(hist) < 2:
        return "neutral"
    prev, curr = float(hist.iloc[-2]), float(hist.iloc[-1])
    if prev <= 0 < curr:
        return "bullish_cross"
    if prev >= 0 > curr:
        return "bearish_cross"
    return "bullish" if curr > 0 else "bearish"
