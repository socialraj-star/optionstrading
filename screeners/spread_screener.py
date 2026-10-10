import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from technical_analysis import get_technicals
from options_data import get_option_chain
from scoring import score_spread
from config import (
    SPY_QQQ, SPREAD_WIDTH, SPREAD_MAX_DEBIT,
    SPREAD_MIN_DTE, SPREAD_MAX_DTE, SPREAD_ITM_DELTA_MIN,
)


def run_spread_screener(symbols: list[str] = None, vix: dict = None) -> tuple[list[dict], dict]:
    if symbols is None:
        symbols = SPY_QQQ
    if vix is None:
        vix = {}

    results = []
    momentum = {}

    for symbol in symbols:
        tech = get_technicals(symbol)
        if tech is None:
            continue

        momentum[symbol] = {
            "price": tech["price"],
            "rsi": tech["rsi"],
            "macd": tech["macd_crossover"],
            "bb_pct": tech["bb_pct"],
            "above_50ma": tech["above_50ma"],
            "hv_rank": tech.get("hv_rank"),
        }

        calls = get_option_chain(symbol, "calls")
        if calls is None or calls.empty:
            continue

        short_dated = calls[
            (calls["dte"] >= SPREAD_MIN_DTE)
            & (calls["dte"] <= SPREAD_MAX_DTE)
            & (calls["bid"] > 0)
        ]

        if short_dated.empty:
            continue

        for expiry, exp_group in short_dated.groupby("expiry"):
            exp_group = exp_group.sort_values("strike").reset_index(drop=True)

            for _, long_leg in exp_group.iterrows():
                long_strike = float(long_leg["strike"])
                long_delta = float(long_leg["delta"])

                if long_delta < SPREAD_ITM_DELTA_MIN:
                    continue

                short_strike = round(long_strike + SPREAD_WIDTH, 2)
                short_rows = exp_group[(exp_group["strike"] - short_strike).abs() < 0.01]
                if short_rows.empty:
                    continue

                short_leg = short_rows.iloc[0]
                net_debit = float(long_leg["mid"]) - float(short_leg["mid"])

                if net_debit <= 0 or net_debit > SPREAD_MAX_DEBIT:
                    continue

                max_profit = SPREAD_WIDTH - net_debit

                trade = {
                    "Symbol": symbol,
                    "Underlying": round(tech["price"], 2),
                    "Buy": long_strike,
                    "Sell": short_strike,
                    "Expiry": expiry,
                    "DTE": int(long_leg["dte"]),
                    "Net Debit": round(net_debit, 2),
                    "Max Profit": round(max_profit, 2),
                    "Max Loss": round(net_debit, 2),
                    "Breakeven": round(long_strike + net_debit, 2),
                    "Long Δ": round(long_delta, 2),
                    "R/R": round(max_profit / net_debit, 2) if net_debit > 0 else 0,
                    "RSI": tech["rsi"],
                    "MACD": "↑" if "bullish" in tech["macd_crossover"] else "↓",
                    "macd_raw": tech["macd_crossover"],
                }

                score, reasons, warnings = score_spread(trade, vix)
                trade["Score"] = score
                trade["Reasons"] = reasons
                trade["Warnings"] = warnings

                results.append(trade)

    results.sort(key=lambda x: x["Score"], reverse=True)
    return results, momentum
