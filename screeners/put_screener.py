import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from technical_analysis import get_technicals
from options_data import get_option_chain, get_iv_rank_for_symbols
from scoring import score_put
from config import (
    PUT_UNIVERSE, PUT_DELTA_RANGE, PUT_MIN_DTE, PUT_MAX_DTE,
    PUT_MIN_PREMIUM_PCT, PULLBACK_MIN_PCT, PULLBACK_MAX_PCT,
)


def run_put_screener(symbols: list[str] = None) -> tuple[list[dict], list[dict]]:
    if symbols is None:
        symbols = PUT_UNIVERSE

    # Batch-fetch IV Rank from Tastytrade for all symbols at once
    iv_ranks = get_iv_rank_for_symbols(symbols)

    results = []
    skipped = []

    for symbol in symbols:
        tech = get_technicals(symbol)
        if tech is None:
            skipped.append({"Symbol": symbol, "Reason": "Price data unavailable"})
            continue

        if not tech["above_50ma"]:
            skipped.append({
                "Symbol": symbol,
                "Reason": f"Below 50 MA (${tech['price']:.2f} < ${tech['ma50']:.2f})",
            })
            continue

        pullback = tech["pullback_pct"]
        if not (PULLBACK_MIN_PCT <= pullback <= PULLBACK_MAX_PCT):
            skipped.append({
                "Symbol": symbol,
                "Reason": f"Pullback {pullback:.1f}% outside {PULLBACK_MIN_PCT}–{PULLBACK_MAX_PCT}% range",
            })
            continue

        puts = get_option_chain(symbol, "puts")
        if puts is None or puts.empty:
            skipped.append({"Symbol": symbol, "Reason": "No options data"})
            continue

        filtered = puts[
            (puts["dte"] >= PUT_MIN_DTE)
            & (puts["dte"] <= PUT_MAX_DTE)
            & (puts["delta"].abs() >= PUT_DELTA_RANGE[0])
            & (puts["delta"].abs() <= PUT_DELTA_RANGE[1])
            & (puts["premium_pct"] >= PUT_MIN_PREMIUM_PCT)
            & (puts["bid"] > 0)
        ]

        if filtered.empty:
            skipped.append({
                "Symbol": symbol,
                "Reason": f"No {PUT_MIN_DTE}–{PUT_MAX_DTE} DTE puts at 0.25–0.35Δ with ≥{PUT_MIN_PREMIUM_PCT}% premium",
            })
            continue

        best = filtered.sort_values("premium_pct", ascending=False).iloc[0]

        macd_cross = tech["macd_crossover"]
        macd_arrow = "↑" if "bullish" in macd_cross else "↓"
        macd_star = " ★" if macd_cross == "bullish_cross" else ""

        days_to_earnings = tech.get("days_to_earnings")
        earnings_date = tech.get("earnings_date", "—")

        # IV Rank from Tastytrade (real) or HV Rank (proxy)
        tt_metrics = iv_ranks.get(symbol, {})
        iv_rank = tt_metrics.get("iv_rank")
        iv_rank_source = "TT" if iv_rank is not None else "HV"
        if iv_rank is None:
            iv_rank = tech.get("hv_rank")

        trade = {
            "Symbol": symbol,
            "Price": round(tech["price"], 2),
            "Strike": float(best["strike"]),
            "Expiry": best["expiry"],
            "DTE": int(best["dte"]),
            "Delta": round(abs(float(best["delta"])), 2),
            "Bid": round(float(best["bid"]), 2),
            "Ask": round(float(best["ask"]), 2),
            "Mid": round(float(best["mid"]), 2),
            "Premium %": round(float(best["premium_pct"]), 1),
            "IV %": round(float(best["impliedVolatility"]) * 100, 1),
            "IV Rank": iv_rank,
            "IV Rank Src": iv_rank_source,
            "HV Rank": tech.get("hv_rank"),
            "RSI": tech["rsi"],
            "MACD": macd_arrow + macd_star,
            "BB %": tech["bb_pct"],
            "Pullback %": round(pullback, 1),
            "Earnings": earnings_date if earnings_date else "—",
            "Days to Earnings": days_to_earnings,
            "P/E": round(tech["pe"], 1) if tech["pe"] else None,
            "macd_raw": macd_cross,
        }

        score, reasons, warnings = score_put(trade)
        trade["Score"] = score
        trade["Reasons"] = reasons
        trade["Warnings"] = warnings

        results.append(trade)

    results.sort(key=lambda x: x["Score"], reverse=True)
    return results, skipped
