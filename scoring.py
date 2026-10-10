"""
Trade scoring (0–100) for each strategy.
Returns (score, [list of reasons why], [list of warnings]).
"""
from datetime import date


# ── PUT SELLING ────────────────────────────────────────────────────────────────

def score_put(trade: dict) -> tuple[int, list[str], list[str]]:
    score = 0
    reasons = []
    warnings = []

    # Premium richness (max 25)
    prem = trade.get("Premium %", 0)
    if prem >= 6:
        score += 25; reasons.append(f"Premium {prem:.1f}% — well above 3% target")
    elif prem >= 4.5:
        score += 18; reasons.append(f"Premium {prem:.1f}% — above target")
    elif prem >= 3:
        score += 10; reasons.append(f"Premium {prem:.1f}% — meets minimum")

    # IV Rank — is premium rich vs history? (max 20)
    # Prefer real IV Rank from Tastytrade, fall back to HV Rank proxy
    ivr = trade.get("IV Rank")
    ivr_src = trade.get("IV Rank Src", "HV")
    ivr_label = "IV Rank" if ivr_src == "TT" else "HV Rank"
    if ivr is not None:
        if ivr >= 70:
            score += 20; reasons.append(f"{ivr_label} {ivr:.0f} — historically rich premium (top 30%)")
        elif ivr >= 50:
            score += 13; reasons.append(f"{ivr_label} {ivr:.0f} — above-average premium")
        elif ivr >= 30:
            score += 6
        else:
            warnings.append(f"{ivr_label} {ivr:.0f} — premium historically cheap, consider waiting")

    # MACD signal (max 20)
    macd = trade.get("MACD", "")
    if "★" in macd or "bullish_cross" in str(trade.get("macd_raw", "")):
        score += 20; reasons.append("MACD bullish crossover — momentum turning up")
    elif "↑" in macd:
        score += 11; reasons.append("MACD bullish trend")
    else:
        warnings.append("MACD not yet bullish — wait for crossover for higher conviction")

    # Pullback quality (max 20)
    pb = trade.get("Pullback %", 0)
    if 6 <= pb <= 10:
        score += 20; reasons.append(f"{pb:.1f}% pullback — ideal entry zone (sweet spot)")
    elif 4 <= pb < 6:
        score += 13; reasons.append(f"{pb:.1f}% pullback — healthy dip")
    elif 10 < pb <= 13:
        score += 10; reasons.append(f"{pb:.1f}% pullback — deeper dip, higher conviction needed")
    elif pb > 13:
        score += 4; warnings.append(f"{pb:.1f}% pullback — steep, potential breakdown, verify support")

    # RSI (max 15)
    rsi = trade.get("RSI", 50)
    if 35 <= rsi <= 50:
        score += 15; reasons.append(f"RSI {rsi:.0f} — oversold but not broken (ideal for put selling)")
    elif 50 < rsi <= 60:
        score += 8; reasons.append(f"RSI {rsi:.0f} — neutral")
    elif rsi < 35:
        score += 5; warnings.append(f"RSI {rsi:.0f} — deeply oversold, wait for stabilization")
    elif rsi > 65:
        warnings.append(f"RSI {rsi:.0f} — overbought, limited upside cushion")

    # Earnings safety (max 10 if safe, penalty if dangerous)
    days_to_earnings = trade.get("Days to Earnings")
    dte = trade.get("DTE", 30)
    if days_to_earnings is not None:
        if days_to_earnings < dte:
            score -= 25
            warnings.append(f"⚠ EARNINGS IN {days_to_earnings}d — falls inside your {dte}d DTE window, HIGH RISK")
        elif days_to_earnings < dte + 7:
            warnings.append(f"Earnings in {days_to_earnings}d — close to expiry, monitor carefully")
        else:
            score += 10; reasons.append(f"Earnings in {days_to_earnings}d — safely past expiry")

    # Delta quality (max 5)
    delta = trade.get("Delta", 0.30)
    if 0.27 <= delta <= 0.33:
        score += 5; reasons.append(f"Delta {delta:.2f} — on target")

    return max(0, min(100, score)), reasons, warnings


# ── LEAPS ─────────────────────────────────────────────────────────────────────

def score_leaps(trade: dict) -> tuple[int, list[str], list[str]]:
    score = 0
    reasons = []
    warnings = []

    # RSI oversold (max 25)
    rsi = trade.get("RSI", 50)
    if rsi <= 30:
        score += 25; reasons.append(f"RSI {rsi:.0f} — strongly oversold, high mean-reversion potential")
    elif rsi <= 35:
        score += 20; reasons.append(f"RSI {rsi:.0f} — oversold")
    elif rsi <= 40:
        score += 12; reasons.append(f"RSI {rsi:.0f} — approaching oversold")

    # Weekly RSI (max 20)
    w_rsi = trade.get("Weekly RSI")
    if w_rsi is not None:
        if w_rsi <= 35:
            score += 20; reasons.append(f"Weekly RSI {w_rsi:.0f} — weekly chart oversold (high conviction)")
        elif w_rsi <= 45:
            score += 12; reasons.append(f"Weekly RSI {w_rsi:.0f} — weekly pullback confirmed")
        elif w_rsi <= 55:
            score += 5
        else:
            warnings.append(f"Weekly RSI {w_rsi:.0f} — weekly trend still strong, daily dip may be noise")

    # BB position (max 20)
    bb_pct = trade.get("BB %", 50)
    if bb_pct <= 15:
        score += 20; reasons.append(f"BB position {bb_pct:.0f}% — near/below lower band (deep oversold)")
    elif bb_pct <= 35:
        score += 13; reasons.append(f"BB position {bb_pct:.0f}% — below midline")
    elif bb_pct <= 50:
        score += 6

    # MACD signal (max 20)
    macd_raw = trade.get("macd_raw", "")
    if "bullish_cross" in macd_raw:
        score += 20; reasons.append("MACD bullish crossover on daily — momentum shifting")
    elif "bullish" in macd_raw:
        score += 10; reasons.append("MACD bullish trend")
    else:
        warnings.append("MACD still bearish — entry may be early, consider scaling in")

    # IV Rank — for LEAPS buying, lower rank = cheaper options (max 10)
    ivr = trade.get("IV Rank")
    ivr_src = trade.get("IV Rank Src", "HV")
    ivr_label = "IV Rank" if ivr_src == "TT" else "HV Rank"
    if ivr is not None:
        if ivr <= 30:
            score += 10; reasons.append(f"{ivr_label} {ivr:.0f} — options relatively cheap (good for buying)")
        elif ivr <= 50:
            score += 5
        else:
            warnings.append(f"{ivr_label} {ivr:.0f} — IV elevated, paying up for the LEAP")

    # Delta proximity to 0.70 (max 5)
    delta = trade.get("Delta", 0.70)
    if abs(delta - 0.70) <= 0.04:
        score += 5; reasons.append(f"Delta {delta:.2f} — on target (0.70)")
    elif abs(delta - 0.70) <= 0.07:
        score += 3

    return max(0, min(100, score)), reasons, warnings


# ── SPY/QQQ SPREADS ────────────────────────────────────────────────────────────

def score_spread(trade: dict, vix: dict) -> tuple[int, list[str], list[str]]:
    score = 0
    reasons = []
    warnings = []

    # Cost efficiency (max 25)
    debit = trade.get("Net Debit", 0.70)
    rr = trade.get("R/R", 1.0)
    if debit <= 0.40:
        score += 25; reasons.append(f"${debit:.2f} debit — excellent cost, max profit ${trade.get('Max Profit', 0):.2f}")
    elif debit <= 0.55:
        score += 18; reasons.append(f"${debit:.2f} debit — strong R/R ({rr:.1f}x)")
    elif debit <= 0.70:
        score += 10; reasons.append(f"${debit:.2f} debit — within threshold")

    # VIX regime (max 20)
    vix_val = vix.get("vix")
    term = vix.get("term_structure", "unknown")
    if vix_val is not None:
        if vix_val < 17 and term == "contango":
            score += 20; reasons.append(f"VIX {vix_val:.1f} + contango — calm market, directional plays work well")
        elif vix_val < 22 and term == "contango":
            score += 13; reasons.append(f"VIX {vix_val:.1f} — manageable volatility environment")
        elif term == "backwardation":
            score -= 10; warnings.append(f"VIX backwardation — fear spike, spreads can gap through strikes")
        elif vix_val >= 30:
            warnings.append(f"VIX {vix_val:.1f} — high volatility, wide bid/ask spreads, execution risk")

    # Momentum (MACD + RSI) (max 25)
    rsi = trade.get("RSI", 50)
    macd_arrow = trade.get("MACD", "")
    if "↑" in macd_arrow and rsi < 60:
        score += 25; reasons.append(f"MACD bullish + RSI {rsi:.0f} — clean momentum setup")
    elif "↑" in macd_arrow:
        score += 15; reasons.append(f"MACD bullish (RSI {rsi:.0f} — watch overbought)")
    elif rsi < 50:
        score += 8
    else:
        warnings.append("MACD not bullish — directional conviction lower")

    if rsi >= 70:
        score -= 10; warnings.append(f"RSI {rsi:.0f} — overbought, poor timing for call spread entry")

    # DTE sweet spot for T+3 exit (max 15)
    dte = trade.get("DTE", 7)
    if 4 <= dte <= 7:
        score += 15; reasons.append(f"{dte} DTE — ideal for T+3 target exit")
    elif 2 <= dte < 4 or 7 < dte <= 10:
        score += 8
    elif dte == 1:
        warnings.append("1 DTE — very short window, binary outcome")

    # Delta (max 15)
    delta = trade.get("Long Δ", 0.55)
    if 0.55 <= delta <= 0.70:
        score += 15; reasons.append(f"Delta {delta:.2f} — slightly ITM, good probability of profit")
    elif 0.50 <= delta < 0.55:
        score += 8
    elif delta > 0.75:
        warnings.append(f"Delta {delta:.2f} — deep ITM, expensive spread")

    return max(0, min(100, score)), reasons, warnings


def score_label(score: int) -> tuple[str, str]:
    """Returns (emoji, label) for a score."""
    if score >= 75:
        return "🟢", "High Conviction"
    elif score >= 55:
        return "🟡", "Moderate"
    elif score >= 35:
        return "🟠", "Low Conviction"
    else:
        return "🔴", "Avoid"
