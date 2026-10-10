import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from technical_analysis import get_technicals
from options_data import get_option_chain, get_iv_rank_for_symbols
from scoring import score_leaps
from config import (
    MAGNIFICENT_10, LEAPS_DELTA_RANGE, LEAPS_TARGET_DELTA,
    LEAPS_MIN_DTE, LEAPS_MAX_DTE, LEAPS_RSI_OVERSOLD,
)


def run_leaps_screener(symbols: list[str] = None) -> tuple[list[dict], list[dict]]:
    if symbols is None:
        symbols = MAGNIFICENT_10

    iv_ranks = get_iv_rank_for_symbols(symbols)

    results = []
    skipped = []

    for symbol in symbols:
        tech = get_technicals(symbol)
        if tech is None:
            skipped.append({"Symbol": symbol, "Reason": "Price data unavailable"})
            continue

        if tech["rsi"] > LEAPS_RSI_OVERSOLD:
            skipped.append({
                "Symbol": symbol,
                "Reason": f"RSI {tech['rsi']} above oversold threshold ({LEAPS_RSI_OVERSOLD})",
            })
            continue

        if not tech["price_below_bb_mid"]:
            skipped.append({
                "Symbol": symbol,
                "Reason": f"Price ${tech['price']:.2f} above BB mid ${tech['bb_mid']:.2f}",
            })
            continue

        calls = get_option_chain(symbol, "calls")
        if calls is None or calls.empty:
            skipped.append({"Symbol": symbol, "Reason": "No options data"})
            continue

        filtered = calls[
            (calls["dte"] >= LEAPS_MIN_DTE)
            & (calls["dte"] <= LEAPS_MAX_DTE)
            & (calls["delta"] >= LEAPS_DELTA_RANGE[0])
            & (calls["delta"] <= LEAPS_DELTA_RANGE[1])
            & (calls["bid"] > 0)
        ]

        if filtered.empty:
            skipped.append({
                "Symbol": symbol,
                "Reason": f"No LEAPS ({LEAPS_MIN_DTE}–{LEAPS_MAX_DTE} DTE) at {LEAPS_DELTA_RANGE[0]}–{LEAPS_DELTA_RANGE[1]}Δ",
            })
            continue

        filtered = filtered.copy()
        filtered["delta_diff"] = (filtered["delta"] - LEAPS_TARGET_DELTA).abs()
        best = filtered.sort_values("delta_diff").iloc[0]

        macd_cross = tech["macd_crossover"]
        macd_arrow = "↑" if "bullish" in macd_cross else "↓"
        weekly_rsi = tech.get("weekly_rsi")

        # IV Rank: for LEAPS buying, lower IV = cheaper options = better
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
            "Delta": round(float(best["delta"]), 2),
            "Bid": round(float(best["bid"]), 2),
            "Ask": round(float(best["ask"]), 2),
            "Mid": round(float(best["mid"]), 2),
            "IV %": round(float(best["impliedVolatility"]) * 100, 1),
            "IV Rank": iv_rank,
            "IV Rank Src": iv_rank_source,
            "HV Rank": tech.get("hv_rank"),
            "RSI": tech["rsi"],
            "Weekly RSI": weekly_rsi,
            "MACD": f"{macd_arrow} {macd_cross.replace('_', ' ').title()}",
            "BB %": tech["bb_pct"],
            "Below BB Mid": "✓",
            "P/E": round(tech["pe"], 1) if tech["pe"] else None,
            "macd_raw": macd_cross,
        }

        score, reasons, warnings = score_leaps(trade)
        trade["Score"] = score
        trade["Reasons"] = reasons
        trade["Warnings"] = warnings

        results.append(trade)

    results.sort(key=lambda x: x["Score"], reverse=True)
    return results, skipped
