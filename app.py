import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import numpy as np
import yfinance as yf

from technical_analysis import get_technicals, _rsi, _macd, _bollinger
from screeners.put_screener import run_put_screener
from screeners.leaps_screener import run_leaps_screener
from screeners.spread_screener import run_spread_screener
from market_movers import get_consistent_movers
from vix_data import get_vix_context
from scoring import score_label
from tastytrade_client import TastytradeClient, DeviceChallengeRequired, trigger_device_challenge
from config import PUT_UNIVERSE, MAGNIFICENT_10, SPY_QQQ
from watchlist_store import load_watchlists, save_watchlist
from portfolio.tastytrade_positions import (
    get_accounts as tt_get_accounts,
    get_positions as tt_get_positions,
    get_balances as tt_get_balances,
    get_transactions as tt_get_transactions,
    get_todays_trade_count,
    backfill_nlv_history,
)
from portfolio.snapshot_store import (
    save_today as snapshot_save_today,
    get_history as snapshot_get_history,
    snapshot_count,
    merge_api_history,
)
from portfolio.schwab_client import SchwabClient
from portfolio.etrade_client import ETradeClient
from portfolio.pnl_calculator import aggregate_positions, portfolio_summary, realized_pnl_by_period, pnl_period_summary, build_net_liq_chart_data
from datetime import datetime
import pytz

st.set_page_config(
    page_title="Options Screener",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Seed watchlists from disk once per session ────────────────────────────────
if "_wl_seeded" not in st.session_state:
    _saved = load_watchlists({"put": PUT_UNIVERSE, "leaps": MAGNIFICENT_10})
    st.session_state["put_watchlist"]   = ", ".join(_saved["put"])
    st.session_state["leaps_watchlist"] = ", ".join(_saved["leaps"])
    st.session_state["_wl_seeded"] = True

st.markdown("""
<style>
.trade-card { border:1px solid #333; border-radius:8px; padding:16px; margin-bottom:12px; }
.score-high  { color:#00d4aa; font-size:2rem; font-weight:700; }
.score-med   { color:#ffd700; font-size:2rem; font-weight:700; }
.score-low   { color:#ff8c00; font-size:2rem; font-weight:700; }
.score-avoid { color:#ff4444; font-size:2rem; font-weight:700; }
</style>
""", unsafe_allow_html=True)


# ── Sidebar: Tastytrade Login ──────────────────────────────────────────────────
with st.sidebar:
    st.header("🔑 Tastytrade")

    if "tt_client" in st.session_state:
        st.success(f"✅ Connected as **{st.session_state.get('tt_username', '')}**")
        st.caption("Real IV Rank active on all screeners")
        if st.button("Disconnect", key="tt_disconnect", use_container_width=True):
            for key in [k for k in st.session_state if k.startswith("tt_")]:
                del st.session_state[key]
            st.rerun()
    else:
        st.caption("Connect for real IV Rank on all screeners")

        # Step 2: device verification code entry
        if "tt_pending_challenge" in st.session_state:
            st.info("📧 Check your email/phone for a verification code from Tastytrade.")
            with st.form("tt_verify"):
                verify_code = st.text_input("Verification code", placeholder="e.g. 123456")
                col1, col2 = st.columns(2)
                verify_btn = col1.form_submit_button("Verify", type="primary", use_container_width=True)
                cancel_btn = col2.form_submit_button("Cancel", use_container_width=True)

            if cancel_btn:
                del st.session_state["tt_pending_challenge"]
                del st.session_state["tt_pending_user"]
                del st.session_state["tt_pending_pass"]
                st.rerun()

            if verify_btn and verify_code:
                pending = st.session_state["tt_pending_challenge"]
                try:
                    client = TastytradeClient(
                        st.session_state["tt_pending_user"],
                        st.session_state["tt_pending_pass"],
                        otp=verify_code.strip(),
                        challenge_token=pending,
                    )
                    st.session_state["tt_client"] = client
                    st.session_state["tt_username"] = st.session_state["tt_pending_user"]
                    for k in ["tt_pending_challenge", "tt_pending_user", "tt_pending_pass"]:
                        del st.session_state[k]
                    st.success("Connected!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Verification failed: {e}")

        # Step 1: username/password
        else:
            with st.form("tt_login"):
                tt_user = st.text_input("Username")
                tt_pass = st.text_input("Password", type="password")
                submitted = st.form_submit_button("Connect", type="primary", use_container_width=True)

                if submitted and tt_user and tt_pass:
                    try:
                        client = TastytradeClient(tt_user, tt_pass)
                        st.session_state["tt_client"] = client
                        st.session_state["tt_username"] = tt_user
                        st.success("Connected!")
                        st.rerun()
                    except DeviceChallengeRequired as e:
                        # Trigger the verification email then move to step 2
                        trigger_device_challenge(e.challenge_token)
                        st.session_state["tt_pending_challenge"] = e.challenge_token
                        st.session_state["tt_pending_user"] = tt_user
                        st.session_state["tt_pending_pass"] = tt_pass
                        st.rerun()
                    except Exception as e:
                        st.error(f"Login failed: {e}")

    st.markdown("---")
    st.caption("Without Tastytrade: screeners use HV Rank (proxy) and yfinance data")

    # ── Schwab Connection ─────────────────────────────────────────────────────
    st.header("📈 Charles Schwab")
    if "schwab_client" in st.session_state and st.session_state["schwab_client"].is_connected():
        st.success("✅ Schwab connected")
        if st.button("Disconnect Schwab", use_container_width=True):
            del st.session_state["schwab_client"]
            st.rerun()
    else:
        with st.expander("Connect Schwab"):
            schwab_id = st.text_input("Client ID", key="schwab_client_id")
            schwab_secret = st.text_input("Client Secret", type="password", key="schwab_client_secret")
            if st.button("Get Auth URL", key="schwab_auth_btn", use_container_width=True) and schwab_id and schwab_secret:
                sc = SchwabClient(schwab_id, schwab_secret)
                st.session_state["schwab_client_pending"] = sc
                url = sc.get_auth_url()
                st.markdown(f"**[Click here to authorize Schwab]({url})**")
                st.caption("After login, copy the full redirect URL (starts with https://127.0.0.1?code=...)")

            if "schwab_client_pending" in st.session_state:
                redirect = st.text_input("Paste redirect URL here", key="schwab_redirect")
                if st.button("Complete Schwab Login", key="schwab_complete", use_container_width=True) and redirect:
                    try:
                        sc = st.session_state["schwab_client_pending"]
                        sc.exchange_code(redirect)
                        st.session_state["schwab_client"] = sc
                        del st.session_state["schwab_client_pending"]
                        st.success("Schwab connected!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Schwab auth failed: {e}")

    # ── E*TRADE Connection ────────────────────────────────────────────────────
    st.header("📊 E*TRADE")
    if "etrade_client" in st.session_state and st.session_state["etrade_client"].is_connected():
        st.success("✅ E*TRADE connected")
        if st.button("Disconnect E*TRADE", use_container_width=True):
            del st.session_state["etrade_client"]
            st.rerun()
    else:
        with st.expander("Connect E*TRADE"):
            et_key = st.text_input("Consumer Key", key="et_consumer_key")
            et_secret = st.text_input("Consumer Secret", type="password", key="et_consumer_secret")
            if st.button("Get Auth URL", key="etrade_auth_btn", use_container_width=True) and et_key and et_secret:
                try:
                    ec = ETradeClient(et_key, et_secret)
                    url = ec.get_auth_url()
                    st.session_state["etrade_client_pending"] = ec
                    st.markdown(f"**[Click here to authorize E*TRADE]({url})**")
                    st.caption("After login, E*TRADE shows a verification code. Paste it below.")
                except Exception as e:
                    st.error(f"Failed to get auth URL: {e}")

            if "etrade_client_pending" in st.session_state:
                verifier = st.text_input("Verification code", key="et_verifier")
                if st.button("Complete E*TRADE Login", key="etrade_complete", use_container_width=True) and verifier:
                    try:
                        ec = st.session_state["etrade_client_pending"]
                        ec.exchange_verifier(verifier.strip())
                        st.session_state["etrade_client"] = ec
                        del st.session_state["etrade_client_pending"]
                        st.success("E*TRADE connected!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"E*TRADE auth failed: {e}")

    st.markdown("---")


# ── Header ────────────────────────────────────────────────────────────────────
col_title, col_refresh = st.columns([5, 1])
with col_title:
    st.title("📊 Options Screener")
    # Market status banner
    now_et = datetime.now(pytz.timezone("America/New_York"))
    weekday = now_et.weekday()   # 0=Mon … 6=Sun
    hour = now_et.hour + now_et.minute / 60
    if weekday >= 5:
        st.warning("⚠️ **Markets closed (weekend)** — prices shown are Friday's last close. Option bid/ask spreads reflect last traded prices and may be stale. Screener still works for planning.")
    elif hour < 9.5 or hour >= 16:
        st.info(f"🕐 **Markets closed (after hours)** — ET {now_et.strftime('%I:%M %p')}. Showing last session's closing prices.")
    else:
        st.caption(f"🟢 **Markets open** · ET {now_et.strftime('%I:%M %p')} · Data 15-min delayed via yfinance · Delta via Black-Scholes")
with col_refresh:
    st.write("")
    if st.button("🔄 Refresh All", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📉 Put Selling",
    "🚀 LEAPS Calls",
    "📈 SPY/QQQ Spreads",
    "📊 Technicals",
    "🔥 Market Movers",
    "💼 Portfolio",
])


# ── VIX widget (shared) ───────────────────────────────────────────────────────
def render_vix_bar(vix: dict):
    if not vix.get("vix"):
        return
    v = vix["vix"]
    chg = vix.get("vix_5d_change", 0)
    rank = vix.get("vix_rank_3m")
    term = vix.get("term_structure", "unknown")
    regime = vix.get("regime", "unknown")
    note = vix.get("regime_note", "")

    regime_colors = {"low": "🔵", "normal": "🟢", "elevated": "🟡", "high": "🔴"}
    icon = regime_colors.get(regime, "⚪")

    with st.container():
        c1, c2, c3, c4 = st.columns(4)
        chg_str = f"+{chg:.2f}" if chg >= 0 else f"{chg:.2f}"
        c1.metric("VIX", f"{v:.1f}", delta=chg_str, delta_color="inverse")
        c2.metric("VIX Rank (3m)", f"{rank:.0f}%" if rank else "—")
        c3.metric("Term Structure", term.title())
        c4.metric("Regime", f"{icon} {regime.title()}")
        st.caption(note)
        st.markdown("---")


# ── Trade card renderer ───────────────────────────────────────────────────────
def render_trade_cards(trades: list[dict], strategy: str):
    top = [t for t in trades if t["Score"] >= 55][:3]
    if not top:
        return

    st.markdown(f"### 🎯 Top {len(top)} Trade Idea{'s' if len(top) > 1 else ''}")
    for trade in top:
        score = trade["Score"]
        emoji, label = score_label(score)
        score_class = (
            "score-high" if score >= 75
            else "score-med" if score >= 55
            else "score-low"
        )
        reasons = trade.get("Reasons", [])
        warnings = trade.get("Warnings", [])

        with st.container(border=True):
            h1, h2 = st.columns([5, 1])
            with h1:
                if strategy == "put":
                    st.markdown(
                        f"**{trade['Symbol']}** — Sell ${trade['Strike']:.0f} Put &nbsp;·&nbsp; "
                        f"{trade['Expiry']} ({trade['DTE']} DTE) &nbsp;·&nbsp; "
                        f"Mid **${trade['Mid']:.2f}** &nbsp;·&nbsp; Premium **{trade['Premium %']:.1f}%** &nbsp;·&nbsp; "
                        f"Δ {trade['Delta']:.2f} &nbsp;·&nbsp; IV {trade['IV %']:.0f}%"
                    )
                    if trade.get("Days to Earnings") is not None:
                        dte = trade["DTE"]
                        days_e = trade["Days to Earnings"]
                        if days_e < dte:
                            st.markdown(f"⚠️ **Earnings in {days_e}d — falls inside expiry window**")
                        else:
                            st.markdown(f"✅ Earnings in {days_e}d (past expiry)")
                elif strategy == "leaps":
                    st.markdown(
                        f"**{trade['Symbol']}** — Buy ${trade['Strike']:.0f} Call &nbsp;·&nbsp; "
                        f"{trade['Expiry']} ({trade['DTE']} DTE) &nbsp;·&nbsp; "
                        f"Mid **${trade['Mid']:.2f}** &nbsp;·&nbsp; Δ {trade['Delta']:.2f} &nbsp;·&nbsp; "
                        f"IV {trade['IV %']:.0f}%"
                        + (f" &nbsp;·&nbsp; Weekly RSI {trade['Weekly RSI']:.0f}" if trade.get("Weekly RSI") else "")
                    )
                elif strategy == "spread":
                    st.markdown(
                        f"**{trade['Symbol']}** — Buy ${trade['Buy']:.0f} / Sell ${trade['Sell']:.0f} Call Spread &nbsp;·&nbsp; "
                        f"{trade['Expiry']} ({trade['DTE']} DTE) &nbsp;·&nbsp; "
                        f"Debit **${trade['Net Debit']:.2f}** &nbsp;·&nbsp; "
                        f"Max profit **${trade['Max Profit']:.2f}** &nbsp;·&nbsp; "
                        f"R/R **{trade['R/R']:.1f}x** &nbsp;·&nbsp; BE ${trade['Breakeven']:.2f}"
                    )

            with h2:
                st.markdown(
                    f"<div style='text-align:center'>"
                    f"<span class='{score_class}'>{score}</span>"
                    f"<div style='font-size:0.75rem;color:#888'>/100 · {label}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

            if reasons:
                r_col, w_col = st.columns(2)
                with r_col:
                    for r in reasons:
                        st.markdown(f"✅ {r}")
                with w_col:
                    for w in warnings:
                        st.markdown(f"⚠️ {w}")
            elif warnings:
                for w in warnings:
                    st.markdown(f"⚠️ {w}")


# ── TAB 1: PUT SCREENER ───────────────────────────────────────────────────────
with tab1:
    st.subheader("Cash-Secured Put Screener")

    with st.spinner("Loading VIX…"):
        vix = get_vix_context()
    render_vix_bar(vix)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Target Delta", "~0.30")
    c2.metric("DTE Range", "25–35 days")
    c3.metric("Min Premium", "3% of strike")
    c4.metric("Filter", "Above 50MA + 3–15% pullback")

    left, right = st.columns([4, 1])
    with right:
        st.write("")
        run_puts = st.button("Run Screener", type="primary", key="run_puts", use_container_width=True)
    with left:
        with st.expander("Watchlist (click to edit)"):
            watchlist_input = st.text_input(
                "Symbols (comma-separated)", key="put_watchlist",
                on_change=lambda: save_watchlist("put", st.session_state["put_watchlist"].split(","))
            )
            put_symbols = [s.strip().upper() for s in watchlist_input.split(",") if s.strip()]

    if run_puts:
        with st.spinner(f"Scanning {len(put_symbols)} symbols…"):
            trades, skipped = run_put_screener(put_symbols)
            st.session_state["put_results"] = trades
            st.session_state["put_skipped"] = skipped

    if "put_results" in st.session_state:
        trades = st.session_state["put_results"]
        skipped = st.session_state.get("put_skipped", [])

        if trades:
            st.success(f"✅ {len(trades)} qualifying put{'s' if len(trades)!=1 else ''} found")
            render_trade_cards(trades, "put")

            st.markdown("### All Qualifying Puts")
            tt_connected = "tt_client" in st.session_state
            display_cols = [
                "Symbol", "Score", "Price", "Strike", "Expiry", "DTE",
                "Delta", "Mid", "Premium %", "IV %", "IV Rank",
                "RSI", "MACD", "BB %", "Pullback %", "Earnings", "P/E",
            ]
            df = pd.DataFrame(trades)[[c for c in display_cols if c in pd.DataFrame(trades).columns]]
            ivr_label = "IV Rank (TT)" if tt_connected else "IV Rank (HV)"
            st.dataframe(
                df, use_container_width=True, hide_index=True,
                column_config={
                    "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
                    "Premium %": st.column_config.NumberColumn(format="%.1f%%"),
                    "IV %": st.column_config.NumberColumn(format="%.1f%%"),
                    "IV Rank": st.column_config.ProgressColumn(ivr_label, min_value=0, max_value=100, format="%d"),
                    "RSI": st.column_config.ProgressColumn("RSI", min_value=0, max_value=100, format="%.0f"),
                    "BB %": st.column_config.ProgressColumn("BB %", min_value=0, max_value=100, format="%.0f"),
                    "Pullback %": st.column_config.NumberColumn(format="%.1f%%"),
                },
            )
            src_note = "IV Rank from Tastytrade (real)" if tt_connected else "IV Rank = HV Rank proxy (connect Tastytrade for real IV Rank)"
            st.caption(f"Sorted by Score (highest conviction first). MACD ★ = bullish crossover. {src_note}")
        else:
            st.warning("No puts met all criteria right now.")

        if skipped:
            with st.expander(f"Filtered out: {len(skipped)} symbols"):
                st.dataframe(pd.DataFrame(skipped), use_container_width=True, hide_index=True)
    else:
        st.info("Click **Run Screener** to scan your watchlist.")


# ── TAB 2: LEAPS SCREENER ─────────────────────────────────────────────────────
with tab2:
    st.subheader("LEAPS Call Screener")

    render_vix_bar(vix)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Target Delta", "0.70")
    c2.metric("DTE Range", "300–730 days")
    c3.metric("RSI Filter", "≤ 40 (oversold)")
    c4.metric("BB Filter", "Price below BB mid")

    left, right = st.columns([4, 1])
    with right:
        st.write("")
        run_leaps = st.button("Run Screener", type="primary", key="run_leaps", use_container_width=True)
    with left:
        with st.expander("Watchlist (click to edit)"):
            leaps_input = st.text_input(
                "Symbols (comma-separated)", key="leaps_watchlist",
                on_change=lambda: save_watchlist("leaps", st.session_state["leaps_watchlist"].split(","))
            )
            leaps_symbols = [s.strip().upper() for s in leaps_input.split(",") if s.strip()]

    if run_leaps:
        with st.spinner(f"Scanning {len(leaps_symbols)} symbols…"):
            trades, skipped = run_leaps_screener(leaps_symbols)
            st.session_state["leaps_results"] = trades
            st.session_state["leaps_skipped"] = skipped

    if "leaps_results" in st.session_state:
        trades = st.session_state["leaps_results"]
        skipped = st.session_state.get("leaps_skipped", [])

        if trades:
            st.success(f"✅ {len(trades)} LEAPS candidate{'s' if len(trades)!=1 else ''} found")
            render_trade_cards(trades, "leaps")

            st.markdown("### All LEAPS Candidates")
            tt_connected = "tt_client" in st.session_state
            display_cols = [
                "Symbol", "Score", "Price", "Strike", "Expiry", "DTE",
                "Delta", "Mid", "IV %", "IV Rank", "RSI", "Weekly RSI",
                "MACD", "BB %", "Below BB Mid", "P/E",
            ]
            df = pd.DataFrame(trades)[[c for c in display_cols if c in pd.DataFrame(trades).columns]]
            ivr_label = "IV Rank (TT)" if tt_connected else "IV Rank (HV)"
            st.dataframe(
                df, use_container_width=True, hide_index=True,
                column_config={
                    "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
                    "IV %": st.column_config.NumberColumn(format="%.1f%%"),
                    "IV Rank": st.column_config.ProgressColumn(ivr_label, min_value=0, max_value=100, format="%d"),
                    "RSI": st.column_config.ProgressColumn("RSI", min_value=0, max_value=100, format="%.0f"),
                    "Weekly RSI": st.column_config.ProgressColumn("W-RSI", min_value=0, max_value=100, format="%.0f"),
                    "BB %": st.column_config.ProgressColumn("BB %", min_value=0, max_value=100, format="%.0f"),
                },
            )
            src_note = "IV Rank from Tastytrade" if tt_connected else "IV Rank = HV Rank proxy"
            st.caption(f"Sorted by Score. Weekly RSI confirms higher-timeframe. {src_note}. Low IV Rank = cheaper options (better for buying).")
        else:
            st.warning("No Magnificent 10 names currently oversold (RSI ≤ 40 + below BB mid).")

        if skipped:
            with st.expander(f"Filtered out: {len(skipped)} symbols"):
                st.dataframe(pd.DataFrame(skipped), use_container_width=True, hide_index=True)
    else:
        st.info("Click **Run Screener** to scan Magnificent 10.")


# ── TAB 3: SPY/QQQ SPREAD SCREENER ────────────────────────────────────────────
with tab3:
    st.subheader("SPY/QQQ $1 Debit Call Spreads")

    render_vix_bar(vix)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Spread Width", "$1.00")
    c2.metric("Max Debit", "$0.70")
    c3.metric("DTE Range", "T+1 to T+14")
    c4.metric("Target Exit", "T+3")

    left, right = st.columns([4, 1])
    with right:
        st.write("")
        run_spreads = st.button("Run Screener", type="primary", key="run_spreads", use_container_width=True)

    if run_spreads:
        with st.spinner("Scanning SPY and QQQ options chains…"):
            trades, momentum = run_spread_screener(vix=vix)
            st.session_state["spread_results"] = trades
            st.session_state["spread_momentum"] = momentum

    if "spread_results" in st.session_state:
        trades = st.session_state["spread_results"]
        momentum = st.session_state.get("spread_momentum", {})

        if momentum:
            st.markdown("**Momentum Context**")
            cols = st.columns(len(momentum))
            for i, (sym, m) in enumerate(momentum.items()):
                with cols[i]:
                    macd_str = m["macd"].replace("_", " ").title()
                    above = "✓" if m["above_50ma"] else "✗"
                    hvr = f"HV Rank {m['hv_rank']:.0f}" if m.get("hv_rank") else ""
                    st.markdown(
                        f"**{sym}** ${m['price']:.2f}  \n"
                        f"RSI: **{m['rsi']:.0f}** · BB: **{m['bb_pct']:.0f}%**  \n"
                        f"MACD: {macd_str} · 50MA: {above}  \n"
                        f"{hvr}"
                    )
            st.markdown("---")

        if trades:
            st.success(f"✅ {len(trades)} spread{'s' if len(trades)!=1 else ''} under $0.70 debit")
            render_trade_cards(trades, "spread")

            st.markdown("### All Qualifying Spreads")
            display_cols = [
                "Symbol", "Score", "Underlying", "Buy", "Sell", "Expiry", "DTE",
                "Net Debit", "Max Profit", "Max Loss", "Breakeven",
                "Long Δ", "R/R", "RSI", "MACD",
            ]
            df = pd.DataFrame(trades)[[c for c in display_cols if c in pd.DataFrame(trades).columns]]
            st.dataframe(
                df, use_container_width=True, hide_index=True,
                column_config={
                    "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
                    "Net Debit": st.column_config.NumberColumn(format="$%.2f"),
                    "Max Profit": st.column_config.NumberColumn(format="$%.2f"),
                    "Max Loss": st.column_config.NumberColumn(format="$%.2f"),
                    "Breakeven": st.column_config.NumberColumn(format="$%.2f"),
                    "Long Δ": st.column_config.NumberColumn(format="%.2f"),
                    "R/R": st.column_config.NumberColumn(format="%.2fx"),
                    "RSI": st.column_config.ProgressColumn("RSI", min_value=0, max_value=100, format="%.0f"),
                },
            )
            st.caption("Sorted by Score (highest conviction first).")
        else:
            st.warning("No $1 call spreads found under $0.70 for current expirations.")
    else:
        st.info("Click **Run Screener** to find SPY/QQQ spread opportunities.")


# ── TAB 4: TECHNICAL VIEW ─────────────────────────────────────────────────────
with tab4:
    st.subheader("Technical Indicator Dashboard")

    all_symbols = sorted(set(PUT_UNIVERSE + MAGNIFICENT_10 + SPY_QQQ))
    symbol = st.selectbox("Select symbol", all_symbols, index=all_symbols.index("NVDA"))

    if symbol:
        with st.spinner(f"Loading {symbol}…"):
            tech = get_technicals(symbol)

        if tech is None:
            st.error(f"Could not load data for {symbol}")
        else:
            hist = tech["hist"]
            close = hist["Close"]

            bb_upper, bb_mid, bb_lower = _bollinger(close)
            macd_line, signal_line, macd_hist_vals = _macd(close)
            rsi_line = _rsi(close)
            ma50 = close.rolling(50).mean()

            m1, m2, m3, m4, m5, m6, m7 = st.columns(7)
            m1.metric("Price", f"${tech['price']:.2f}")
            m2.metric("RSI (14)", f"{tech['rsi']:.0f}",
                      delta="Oversold" if tech['rsi'] < 35 else ("Overbought" if tech['rsi'] > 65 else "Neutral"))
            m3.metric("Weekly RSI", f"{tech['weekly_rsi']:.0f}" if tech.get("weekly_rsi") else "—")
            m4.metric("HV Rank", f"{tech['hv_rank']:.0f}%" if tech.get("hv_rank") else "—",
                      help="Historical Volatility Rank — proxy for IV Rank. ≥50 = rich premium.")
            m5.metric("BB Position", f"{tech['bb_pct']:.0f}%", help="0%=lower band, 100%=upper band")
            m6.metric("Pullback from 20d High", f"{tech['pullback_pct']:.1f}%")
            m7.metric("Earnings", tech.get("earnings_date") or "N/A",
                      delta=f"in {tech['days_to_earnings']}d" if tech.get("days_to_earnings") else None)
            st.markdown("---")

            fig = make_subplots(
                rows=3, cols=1, shared_xaxes=True,
                row_heights=[0.55, 0.25, 0.20],
                vertical_spacing=0.03,
                subplot_titles=(f"{symbol} — Price & Bollinger Bands", "MACD", "RSI (14)"),
            )

            fig.add_trace(go.Scatter(x=hist.index, y=bb_upper,
                line=dict(color="rgba(150,150,150,0.4)", width=1), name="BB Upper", showlegend=False), row=1, col=1)
            fig.add_trace(go.Scatter(x=hist.index, y=bb_lower,
                line=dict(color="rgba(150,150,150,0.4)", width=1), fill="tonexty",
                fillcolor="rgba(150,150,150,0.08)", name="BB Band", showlegend=False), row=1, col=1)
            fig.add_trace(go.Scatter(x=hist.index, y=bb_mid,
                line=dict(color="orange", dash="dot", width=1), name="BB Mid"), row=1, col=1)
            fig.add_trace(go.Scatter(x=hist.index, y=close,
                line=dict(color="#00d4aa", width=2), name=symbol), row=1, col=1)
            fig.add_trace(go.Scatter(x=hist.index, y=ma50,
                line=dict(color="#5599ff", width=1, dash="dash"), name="50 MA"), row=1, col=1)

            colors = ["#00d4aa" if v >= 0 else "#ff4444" for v in macd_hist_vals]
            fig.add_trace(go.Bar(x=hist.index, y=macd_hist_vals,
                marker_color=colors, name="MACD Hist", showlegend=False), row=2, col=1)
            fig.add_trace(go.Scatter(x=hist.index, y=macd_line,
                line=dict(color="#5599ff", width=1.5), name="MACD"), row=2, col=1)
            fig.add_trace(go.Scatter(x=hist.index, y=signal_line,
                line=dict(color="orange", width=1.5), name="Signal"), row=2, col=1)

            fig.add_trace(go.Scatter(x=hist.index, y=rsi_line,
                line=dict(color="#cc88ff", width=1.5), name="RSI", showlegend=False), row=3, col=1)
            fig.add_hline(y=70, line_dash="dash", line_color="red", line_width=1, row=3, col=1)
            fig.add_hline(y=40, line_dash="dash", line_color="green", line_width=1, row=3, col=1)
            fig.add_hline(y=30, line_dash="dash", line_color="green", line_width=1, row=3, col=1)

            fig.update_layout(
                template="plotly_dark", height=650,
                margin=dict(l=10, r=10, t=40, b=10),
                legend=dict(orientation="h", y=1.02),
                xaxis3_rangeslider_visible=False,
            )
            fig.update_yaxes(title_text="Price", row=1, col=1)
            fig.update_yaxes(title_text="MACD", row=2, col=1)
            fig.update_yaxes(title_text="RSI", row=3, col=1, range=[0, 100])
            st.plotly_chart(fig, use_container_width=True)

            st.markdown("**Signal Summary**")
            signals = []
            rsi_val = tech["rsi"]
            w_rsi = tech.get("weekly_rsi")
            hvr = tech.get("hv_rank")

            if rsi_val < 30:
                signals.append(("🟢", "RSI (daily)", f"{rsi_val:.0f} — Strongly oversold"))
            elif rsi_val < 40:
                signals.append(("🟡", "RSI (daily)", f"{rsi_val:.0f} — Oversold"))
            elif rsi_val > 70:
                signals.append(("🔴", "RSI (daily)", f"{rsi_val:.0f} — Overbought"))
            else:
                signals.append(("⚪", "RSI (daily)", f"{rsi_val:.0f} — Neutral"))

            if w_rsi is not None:
                if w_rsi < 40:
                    signals.append(("🟢", "RSI (weekly)", f"{w_rsi:.0f} — Weekly chart oversold (high conviction)"))
                elif w_rsi > 65:
                    signals.append(("🔴", "RSI (weekly)", f"{w_rsi:.0f} — Weekly still strong"))
                else:
                    signals.append(("⚪", "RSI (weekly)", f"{w_rsi:.0f} — Weekly neutral"))

            if hvr is not None:
                if hvr >= 65:
                    signals.append(("🟢", "HV Rank", f"{hvr:.0f} — Premium rich, good for selling"))
                elif hvr <= 30:
                    signals.append(("🟢", "HV Rank", f"{hvr:.0f} — Volatility cheap, good for buying LEAPS"))
                else:
                    signals.append(("⚪", "HV Rank", f"{hvr:.0f} — Average volatility"))

            macd_cross = tech["macd_crossover"]
            if "bullish_cross" in macd_cross:
                signals.append(("🟢", "MACD", "Bullish crossover ★"))
            elif "bullish" in macd_cross:
                signals.append(("🟡", "MACD", "Bullish trend"))
            elif "bearish_cross" in macd_cross:
                signals.append(("🔴", "MACD", "Bearish crossover"))
            else:
                signals.append(("🔴", "MACD", "Bearish trend"))

            bb_pct = tech["bb_pct"]
            if bb_pct < 20:
                signals.append(("🟢", "Bollinger", f"BB {bb_pct:.0f}% — Near lower band (oversold)"))
            elif bb_pct > 80:
                signals.append(("🔴", "Bollinger", f"BB {bb_pct:.0f}% — Near upper band (overbought)"))
            else:
                signals.append(("⚪", "Bollinger", f"BB {bb_pct:.0f}% — Mid range"))

            if tech["above_50ma"]:
                pb = tech["pullback_pct"]
                if 5 <= pb <= 10:
                    signals.append(("🟢", "Trend", f"Above 50MA with {pb:.1f}% pullback — ideal for puts"))
                elif 3 <= pb < 5:
                    signals.append(("🟡", "Trend", f"Above 50MA, {pb:.1f}% pullback — light dip"))
                else:
                    signals.append(("🟡", "Trend", f"Above 50MA, {pb:.1f}% pullback"))
            else:
                signals.append(("🔴", "Trend", "Below 50MA — avoid put selling"))

            if tech.get("days_to_earnings") is not None:
                d = tech["days_to_earnings"]
                if d <= 14:
                    signals.append(("🔴", "Earnings", f"In {d} days — high risk window"))
                elif d <= 35:
                    signals.append(("🟡", "Earnings", f"In {d} days — falls inside typical DTE window"))
                else:
                    signals.append(("🟢", "Earnings", f"In {d} days — safe"))

            for icon, label, desc in signals:
                st.markdown(f"{icon} **{label}**: {desc}")


# ── TAB 5: MARKET MOVERS ──────────────────────────────────────────────────────
with tab5:
    st.subheader("Consistent Market Movers — Watchlist Builder")
    st.caption(
        "Scans ~100 actively traded stocks and finds names that appeared in the top 10 "
        "biggest daily movers on 3+ of the last 14 trading days. "
        "High-mover stocks tend to carry elevated IV — good for put selling premium."
    )

    col_params, col_run = st.columns([3, 1])
    with col_params:
        c1, c2, c3 = st.columns(3)
        lookback = c1.number_input("Lookback days", min_value=5, max_value=30, value=14, step=1)
        top_n = c2.number_input("Top N per day", min_value=5, max_value=20, value=10, step=1)
        min_days = c3.number_input("Min appearances", min_value=2, max_value=14, value=3, step=1)
    with col_run:
        st.write("")
        run_movers = st.button("Find Movers", type="primary", use_container_width=True, key="run_movers")
        st.caption("~20 sec first run (cached 4h)")

    if run_movers:
        with st.spinner(f"Downloading {lookback}-day history for ~100 symbols…"):
            movers = get_consistent_movers(
                days=int(lookback),
                top_n_per_day=int(top_n),
                min_appearances=int(min_days),
            )
            st.session_state["movers_results"] = movers

    if "movers_results" in st.session_state:
        movers = st.session_state["movers_results"]
        if not movers:
            st.warning("No stocks met the criteria. Try lowering Min Appearances.")
        else:
            st.success(f"✅ {len(movers)} consistent movers found")
            df = pd.DataFrame(movers)
            existing = set(PUT_UNIVERSE + MAGNIFICENT_10)
            df["In Watchlist"] = df["Symbol"].apply(lambda s: "✓" if s in existing else "➕ New")
            st.dataframe(
                df, use_container_width=True, hide_index=True,
                column_config={
                    "Days in Top 10": st.column_config.ProgressColumn(
                        "Days in Top 10", min_value=0, max_value=int(lookback), format="%d"
                    ),
                    "Avg Daily Move %": st.column_config.NumberColumn(format="%.2f%%"),
                    "Max Daily Move %": st.column_config.NumberColumn(format="%.2f%%"),
                },
            )
            new_names = df[df["In Watchlist"] == "➕ New"]["Symbol"].tolist()
            if new_names:
                st.markdown("---")
                st.markdown("**New names not on your current watchlists:**")
                st.code(", ".join(new_names))
                st.caption("Copy and paste into the Watchlist field on the Put Selling tab.")
    else:
        st.info("Click **Find Movers** to scan for active names.")


# ── TAB 6: PORTFOLIO ──────────────────────────────────────────────────────────
with tab6:
    connected_brokers = []
    if "tt_client" in st.session_state:
        connected_brokers.append("Tastytrade")
    if "schwab_client" in st.session_state and st.session_state["schwab_client"].is_connected():
        connected_brokers.append("Schwab")
    if "etrade_client" in st.session_state and st.session_state["etrade_client"].is_connected():
        connected_brokers.append("E*TRADE")

    if not connected_brokers:
        st.subheader("Portfolio")
        st.info(
            "Connect at least one broker in the sidebar to view your portfolio.\n\n"
            "**Available:** Tastytrade (username/password) · Schwab (OAuth) · E*TRADE (OAuth)"
        )
    else:
        # ── Top toolbar ───────────────────────────────────────────────────────
        tb_left, tb_mid, tb_right = st.columns([2, 3, 2])
        with tb_left:
            load_portfolio = st.button("⟳  Load Portfolio", type="primary", use_container_width=True)
        with tb_mid:
            # Account selector: build options after load
            acct_options = list(st.session_state.get("portfolio_balances", {}).keys()) or ["All Accounts"]
            if len(acct_options) > 1:
                acct_options = ["All Accounts"] + acct_options
            selected_account = st.selectbox(
                "Account", acct_options, label_visibility="collapsed"
            )
        with tb_right:
            pnl_period = st.selectbox(
                "P&L Period",
                ["Today", "This Week", "This Month", "This Quarter", "This Year", "All Time"],
                index=2,
                label_visibility="collapsed",
            )

        # ── Data fetch ────────────────────────────────────────────────────────
        if load_portfolio:
            all_positions = {}
            all_transactions = []
            broker_balances = {}
            tt_acct_map = {}   # label → account_number (for backfill)
            today_trades = {}
            errors = []

            # Tastytrade
            if "tt_client" in st.session_state:
                try:
                    tt = st.session_state["tt_client"]
                    tt_accounts = tt_get_accounts(tt)
                    for acct in tt_accounts:
                        an = acct["account_number"]
                        label = f"Tastytrade ({acct.get('nickname', an)})"
                        all_positions[label] = tt_get_positions(tt, an)
                        broker_balances[label] = tt_get_balances(tt, an)
                        txns = tt_get_transactions(tt, an)
                        all_transactions.extend(txns)
                        today_trades[label] = get_todays_trade_count(tt, an)
                        tt_acct_map[label] = an
                except Exception as e:
                    errors.append(f"Tastytrade: {e}")

            # Schwab
            if "schwab_client" in st.session_state:
                try:
                    sc = st.session_state["schwab_client"]
                    schwab_accounts = sc.get_accounts()
                    for acct in schwab_accounts:
                        an    = acct["account_number"]
                        label = f"Schwab ({acct.get('nickname', an)})"
                        all_positions[label] = sc.get_positions(an)
                        broker_balances[label] = {
                            "net_liquidating_value":   acct.get("net_liquidating_value"),
                            "cash":                    acct.get("cash"),
                            "equity_buying_power":     acct.get("equity_buying_power"),
                            "derivative_buying_power": acct.get("derivative_buying_power"),
                        }
                        txns = sc.get_transactions(an)
                        all_transactions.extend(txns)
                        today_trades[label] = sc.get_todays_trade_count(an)
                except Exception as e:
                    errors.append(f"Schwab: {e}")

            # E*TRADE
            if "etrade_client" in st.session_state:
                try:
                    ec = st.session_state["etrade_client"]
                    et_accounts = ec.get_accounts()
                    for acct in et_accounts:
                        an = acct["account_number"]
                        label = f"E*TRADE ({acct.get('nickname', an)})"
                        all_positions[label] = ec.get_positions(an)
                        txns = ec.get_transactions(an)
                        all_transactions.extend(txns)
                except Exception as e:
                    errors.append(f"E*TRADE: {e}")

            # Save today's NLV snapshot
            if broker_balances:
                snapshot_save_today(broker_balances)

            st.session_state["portfolio_positions"]    = all_positions
            st.session_state["portfolio_transactions"] = all_transactions
            st.session_state["portfolio_balances"]     = broker_balances
            st.session_state["portfolio_today_trades"] = today_trades
            st.session_state["portfolio_tt_acct_map"]  = tt_acct_map
            st.session_state["portfolio_raw_debug"]    = {
                "positions_sample": {k: v[:2] for k, v in all_positions.items() if v},
                "transactions_sample": all_transactions[:3],
                "balances": {k: {ky: v for ky, v in b.items() if not ky.startswith("_")}
                             for k, b in broker_balances.items()},
            }
            if errors:
                for err in errors:
                    st.warning(f"⚠️ {err}")

        # ── Render portfolio ──────────────────────────────────────────────────
        if "portfolio_positions" in st.session_state:
            all_positions    = st.session_state["portfolio_positions"]
            all_transactions = st.session_state["portfolio_transactions"]
            broker_balances  = st.session_state.get("portfolio_balances", {})
            today_trades     = st.session_state.get("portfolio_today_trades", {})
            tt_acct_map      = st.session_state.get("portfolio_tt_acct_map", {})

            # Filter to selected account if not "All Accounts"
            if selected_account != "All Accounts" and selected_account in broker_balances:
                view_positions = {selected_account: all_positions.get(selected_account, [])}
                view_balances  = {selected_account: broker_balances[selected_account]}
            else:
                view_positions = all_positions
                view_balances  = broker_balances

            positions_df = aggregate_positions(view_positions)
            summary      = portfolio_summary(positions_df)

            # ── Aggregate key balance numbers ────────────────────────────────
            total_nlv     = sum(b.get("net_liquidating_value") or 0 for b in view_balances.values())
            total_cash    = sum(b.get("cash") or 0 for b in view_balances.values())
            total_opt_bp  = sum(b.get("derivative_buying_power") or 0 for b in view_balances.values())
            total_eq_bp   = sum(b.get("equity_buying_power") or 0 for b in view_balances.values())
            total_cry_bp  = sum(b.get("crypto_buying_power") or 0 for b in view_balances.values())
            total_today   = sum(today_trades.values())
            unreal        = summary.get("total_unrealized_pnl", 0)
            day_pnl       = summary.get("total_day_pnl", 0)

            if not total_nlv:
                total_nlv = summary.get("total_market_value", 0)

            # ── Realized P&L (needed for summary table) ──────────────────────
            realized = realized_pnl_by_period(all_transactions)

            # ── MAIN DASHBOARD: chart + metrics side-by-side ─────────────────
            st.markdown("---")
            chart_col, metrics_col = st.columns([3, 1], gap="medium")

            # ── LEFT: Portfolio chart ─────────────────────────────────────────
            with chart_col:
                # Period selector buttons
                period_map = {"1D": 1, "1W": 7, "1M": 30, "MTD": None, "YTD": None, "1Y": 365}
                btn_cols = st.columns(len(period_map))
                if "chart_period" not in st.session_state:
                    st.session_state["chart_period"] = "1Y"
                for col, label in zip(btn_cols, period_map):
                    if col.button(label, key=f"cp_{label}",
                                  type="primary" if st.session_state["chart_period"] == label else "secondary",
                                  use_container_width=True):
                        st.session_state["chart_period"] = label
                        st.rerun()

                from datetime import date as _date, timedelta as _td

                cp = st.session_state["chart_period"]
                if cp == "MTD":
                    mtd_start    = _date.today().replace(day=1)
                    chart_days   = (_date.today() - mtd_start).days + 1
                    period_label = f"MTD (since {mtd_start.strftime('%b 1')})"
                elif cp == "YTD":
                    ytd_start    = _date.today().replace(month=1, day=1)
                    chart_days   = (_date.today() - ytd_start).days + 1
                    period_label = "YTD (since Jan 1)"
                else:
                    chart_days   = period_map[cp] or 365
                    period_label = cp

                local_histories = snapshot_get_history(days=chart_days)

                # Filter histories to selected account if applicable
                if selected_account != "All Accounts":
                    local_histories = {k: v for k, v in local_histories.items()
                                       if k == selected_account}

                chart_df = build_net_liq_chart_data(local_histories)

                if not chart_df.empty and len(chart_df) > 1:
                    # Actual data range
                    actual_start = pd.to_datetime(chart_df["date"].iloc[0]).date()
                    actual_end   = pd.to_datetime(chart_df["date"].iloc[-1]).date()
                    n_pts        = len(chart_df)
                    requested_start = _date.today() - _td(days=chart_days - 1)

                    # If period start is further back than our earliest data,
                    # trim chart_df to the requested window (no extra data shown)
                    # and flag if data is clipped
                    data_clipped = actual_start > requested_start

                    start_val  = chart_df["Total"].iloc[0]
                    end_val    = chart_df["Total"].iloc[-1]

                    # Green/red area based on direction vs period start
                    is_up      = end_val >= start_val
                    line_color = "#4CAF50" if is_up else "#ff4444"
                    fill_color = "rgba(76,175,80,0.12)" if is_up else "rgba(255,68,68,0.10)"

                    fig_nlv = go.Figure()
                    fig_nlv.add_trace(go.Scatter(
                        x=chart_df["date"], y=chart_df["Total"],
                        mode="lines",
                        name="Portfolio",
                        line=dict(color=line_color, width=2.5),
                        fill="tozeroy",
                        fillcolor=fill_color,
                        hovertemplate="<b>%{x|%b %d, %Y}</b><br>$%{y:,.0f}<extra></extra>",
                    ))

                    # Horizontal reference line at period-start value
                    fig_nlv.add_hline(
                        y=start_val,
                        line=dict(color="rgba(255,255,255,0.15)", width=1, dash="dot"),
                    )

                    change     = end_val - start_val
                    change_pct = change / start_val * 100 if start_val else 0
                    dir_arrow  = "▲" if change >= 0 else "▼"
                    dir_color  = "#4CAF50" if change >= 0 else "#ff4444"

                    # X-axis range: pin left edge to the requested period start
                    # (or earliest data if we don't have that far back)
                    x_start = max(requested_start, actual_start).isoformat()
                    fig_nlv.update_layout(
                        template="plotly_dark",
                        height=320,
                        margin=dict(l=0, r=0, t=10, b=0),
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        yaxis=dict(tickformat="$,.0f", showgrid=True,
                                   gridcolor="rgba(255,255,255,0.05)"),
                        xaxis=dict(showgrid=False, range=[x_start, actual_end.isoformat()]),
                        hovermode="x unified",
                        showlegend=False,
                    )
                    st.plotly_chart(fig_nlv, use_container_width=True)

                    # Caption line: change + actual date range
                    date_range_str = (
                        f"{actual_start.strftime('%b %d, %Y')} – {actual_end.strftime('%b %d, %Y')}"
                    )
                    clip_note = (
                        f" · ⚠ {period_label} starts {requested_start.strftime('%b %d')} "
                        f"but earliest data is {actual_start.strftime('%b %d')} — run Backfill to extend"
                        if data_clipped else ""
                    )
                    st.markdown(
                        f"<span style='color:{dir_color};font-size:0.95rem'>"
                        f"{dir_arrow} ${abs(change):,.0f} ({change_pct:+.1f}%)</span>"
                        f"<span style='color:#888;font-size:0.8rem;margin-left:12px'>"
                        f"{date_range_str} · {n_pts} days</span>"
                        f"<span style='color:#c8a000;font-size:0.78rem'>{clip_note}</span>",
                        unsafe_allow_html=True,
                    )

                else:
                    # Not enough history — show a placeholder with today's value
                    n_pts = snapshot_count()
                    st.metric("Portfolio Value", f"${total_nlv:,.2f}")
                    st.info(
                        f"📈 {n_pts} day{'s' if n_pts!=1 else ''} of history stored. "
                        "**Load the portfolio daily** to build the chart, or click "
                        "**Fetch Historical Data** below to backfill up to 1 year instantly."
                    )

                # ── Backfill button (Tastytrade only) ─────────────────────────
                if tt_acct_map:
                    with st.expander("📥 Fetch historical chart data (one-time setup)"):
                        st.caption(
                            "Makes ~250 API calls to Tastytrade to pull 1 year of daily NLV. "
                            "Takes ~60–90 seconds. Run once to seed the chart."
                        )
                        backfill_days = st.slider("Days to backfill", 30, 365, 365, step=30)
                        if st.button("Start Backfill", type="secondary"):
                            tt = st.session_state["tt_client"]
                            added = 0
                            with st.spinner(f"Fetching {backfill_days} days of history…"):
                                for label, an in tt_acct_map.items():
                                    if selected_account != "All Accounts" and label != selected_account:
                                        continue
                                    records = backfill_nlv_history(tt, an, days=backfill_days)
                                    added += merge_api_history(label, records)
                            st.success(f"Backfill complete — {added} records added. Reload the page.")

            # ── RIGHT: Metrics panel ──────────────────────────────────────────
            with metrics_col:
                st.markdown("""
                <style>
                .tt-metric-block { padding: 6px 0; }
                .tt-metric-label { color: #888; font-size: 0.78rem; text-transform: uppercase;
                                   letter-spacing: 0.04em; margin-bottom: 2px; }
                .tt-metric-value { color: #e8e8e8; font-size: 1.4rem; font-weight: 600; }
                .tt-metric-value.positive { color: #4CAF50; }
                .tt-metric-value.negative { color: #ff4444; }
                .tt-divider { border-top: 1px solid #2a2a2a; margin: 8px 0; }
                </style>
                """, unsafe_allow_html=True)

                def _metric(label: str, value: float | None, highlight: bool = False):
                    """Render a TT-style label+value block."""
                    if value is None:
                        return
                    cls = ""
                    if highlight:
                        cls = "positive" if value >= 0 else "negative"
                    st.markdown(
                        f'<div class="tt-metric-block">'
                        f'<div class="tt-metric-label">{label}</div>'
                        f'<div class="tt-metric-value {cls}">${value:,.2f}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                _metric("Account Net Liq", total_nlv)
                st.markdown('<div class="tt-divider"></div>', unsafe_allow_html=True)
                _metric("Cash", total_cash)

                if total_opt_bp:
                    _metric("Option Buying Power", total_opt_bp)
                if total_eq_bp:
                    _metric("Stock Buying Power", total_eq_bp)
                if total_cry_bp:
                    _metric("Crypto Buying Power", total_cry_bp)

                st.markdown('<div class="tt-divider"></div>', unsafe_allow_html=True)
                _metric("Open P&L", unreal, highlight=True)
                _metric("Day P&L", day_pnl, highlight=True)

                st.markdown('<div class="tt-divider"></div>', unsafe_allow_html=True)
                st.markdown(
                    f'<div class="tt-metric-block">'
                    f'<div class="tt-metric-label">Today\'s Trades</div>'
                    f'<div class="tt-metric-value">{total_today}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

                pos_count = summary.get("position_count", 0)
                winners   = summary.get("winners", 0)
                losers    = summary.get("losers", 0)
                st.markdown(
                    f'<div class="tt-metric-block">'
                    f'<div class="tt-metric-label">Positions</div>'
                    f'<div class="tt-metric-value">{pos_count}</div>'
                    f'</div>'
                    f'<div class="tt-metric-block">'
                    f'<div class="tt-metric-label">Winners / Losers</div>'
                    f'<div class="tt-metric-value">'
                    f'<span class="positive">{winners}</span>'
                    f'<span style="color:#888"> / </span>'
                    f'<span class="negative">{losers}</span>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )

            st.markdown("---")

            # ── P&L summary table ─────────────────────────────────────────────
            st.markdown("### P&L Summary")

            pnl_rows = pnl_period_summary(
                realized,
                positions_df=positions_df,
                transactions=all_transactions,
                portfolio_nlv=total_nlv,
            )
            pnl_df   = pd.DataFrame(pnl_rows)

            def _color_pnl(val):
                if isinstance(val, (int, float)):
                    return f"color: {'#4CAF50' if val >= 0 else '#ff4444'}"
                return ""

            styled = pnl_df.style.map(
                _color_pnl, subset=["Realized P&L", "Unrealized P&L", "Unrealized %"]
            ).format({
                "Realized P&L":   lambda v: f"${v:,.2f}",
                "Unrealized P&L": lambda v: f"${v:,.2f}",
                "Unrealized %":   lambda v: f"{v:+.2f}%" if v is not None else "—",
                "Closed Trades":  lambda v: f"{int(v):,}",
            })
            st.dataframe(
                styled,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Period":         st.column_config.TextColumn("Period"),
                    "Realized P&L":   st.column_config.TextColumn("Realized P&L"),
                    "Closed Trades":  st.column_config.TextColumn("Closed Trades"),
                    "Unrealized P&L": st.column_config.TextColumn("Unrealized P&L"),
                    "Unrealized %":   st.column_config.TextColumn("Unrealized % of Portfolio"),
                },
            )
            st.caption(
                "Realized P&L = FIFO matched open/close pairs, recognised on closing date. "
                "Unrealized P&L = mark-to-market on positions whose open date falls within that period "
                "(All Time row = total across all open positions). "
                "Positions opened before the 12-month transaction history window are excluded from sub-period rows."
            )

            st.markdown("---")

            # ── Monthly P&L bar chart ─────────────────────────────────────────
            monthly_pnl = realized.get("monthly", {})
            if monthly_pnl:
                st.markdown("### Monthly Realized P&L")
                months = sorted(monthly_pnl.keys())
                values = [monthly_pnl[m] for m in months]
                bar_colors = ["#4CAF50" if v >= 0 else "#ff4444" for v in values]

                fig_monthly = go.Figure(go.Bar(
                    x=months, y=values,
                    marker_color=bar_colors,
                    text=[f"${v:,.0f}" for v in values],
                    textposition="outside",
                ))
                fig_monthly.update_layout(
                    template="plotly_dark",
                    height=300,
                    margin=dict(l=10, r=10, t=20, b=10),
                    yaxis_title="Net P&L ($)",
                    yaxis_tickformat="$,.0f",
                    paper_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(fig_monthly, use_container_width=True)
                st.caption(
                    "📌 Matched-lot basis: P&L recognised on closing date only. "
                    "Positions opened before the 12-month history window show ⚠ (open cost unknown)."
                )

            st.markdown("---")

            # ── Allocation charts ─────────────────────────────────────────────
            c1, c2 = st.columns(2)
            with c1:
                by_broker = summary.get("by_broker", {})
                if by_broker:
                    fig_b = go.Figure(go.Pie(
                        labels=list(by_broker.keys()), values=list(by_broker.values()),
                        hole=0.45, textinfo="label+percent",
                    ))
                    fig_b.update_layout(title="Allocation by Broker",
                                        template="plotly_dark", height=300,
                                        margin=dict(l=10, r=10, t=40, b=10), showlegend=False)
                    st.plotly_chart(fig_b, use_container_width=True)

            with c2:
                by_type = summary.get("by_type", {})
                if by_type:
                    fig_t = go.Figure(go.Pie(
                        labels=list(by_type.keys()), values=[abs(v) for v in by_type.values()],
                        hole=0.45, textinfo="label+percent",
                    ))
                    fig_t.update_layout(title="Allocation by Type",
                                        template="plotly_dark", height=300,
                                        margin=dict(l=10, r=10, t=40, b=10), showlegend=False)
                    st.plotly_chart(fig_t, use_container_width=True)

            st.markdown("---")

            # ── Positions table ───────────────────────────────────────────────
            st.markdown("### All Positions")
            if not positions_df.empty:
                display_cols = [
                    "broker", "symbol", "instrument_type", "direction", "quantity",
                    "cost_basis", "close_price", "market_value",
                    "unrealized_pnl", "unrealized_pnl_pct", "day_pnl",
                ]
                show_df = positions_df[[c for c in display_cols if c in positions_df.columns]].copy()
                show_df = show_df.sort_values("market_value", key=lambda x: x.abs(), ascending=False)

                col_cfg = {
                    "broker":             st.column_config.TextColumn("Broker"),
                    "symbol":             st.column_config.TextColumn("Symbol"),
                    "instrument_type":    st.column_config.TextColumn("Type"),
                    "direction":          st.column_config.TextColumn("L/S"),
                    "quantity":           st.column_config.NumberColumn("Qty", format="%.0f"),
                    "cost_basis":         st.column_config.NumberColumn("Cost Basis", format="$%.4f"),
                    "close_price":        st.column_config.NumberColumn("Last", format="$%.4f"),
                    "market_value":       st.column_config.NumberColumn("Mkt Value", format="$%.2f"),
                    "unrealized_pnl":     st.column_config.NumberColumn("Open P&L", format="$%.2f"),
                    "unrealized_pnl_pct": st.column_config.NumberColumn("Open %", format="%.2f%%"),
                    "day_pnl":            st.column_config.NumberColumn("Day P&L", format="$%.2f"),
                }
                st.dataframe(show_df, use_container_width=True, hide_index=True, column_config=col_cfg)

            # ── Closed trades detail ──────────────────────────────────────────
            trades_list = realized.get("trades", [])
            if trades_list:
                matched_count   = sum(1 for t in trades_list if t.get("matched"))
                unmatched_count = len(trades_list) - matched_count
                label = f"📋 Closed trade lots ({matched_count} matched"
                if unmatched_count:
                    label += f", {unmatched_count} ⚠ unmatched"
                label += ")"
                with st.expander(label):
                    keep_cols = ["date", "symbol", "quantity", "pnl",
                                 "close_action", "open_date", "open_action", "matched"]
                    trades_df = pd.DataFrame(trades_list)
                    trades_df = trades_df[[c for c in keep_cols if c in trades_df.columns]].copy()
                    trades_df = trades_df.sort_values("date", ascending=False)
                    trades_df["matched"] = trades_df["matched"].map(
                        {True: "✅", False: "⚠ unmatched"}
                    )
                    rename = {
                        "date": "Close Date", "symbol": "Symbol",
                        "quantity": "Qty", "pnl": "Realized P&L",
                        "close_action": "Close Action", "open_date": "Open Date",
                        "open_action": "Open Action", "matched": "Status",
                    }
                    trades_df = trades_df.rename(columns=rename)
                    styled_t = trades_df.style.map(
                        lambda val: (
                            f"color: {'#4CAF50' if val >= 0 else '#ff4444'}"
                            if isinstance(val, (int, float)) else ""
                        ),
                        subset=["Realized P&L"]
                    ).format({
                        "Realized P&L": "${:,.2f}",
                        "Qty": "{:.0f}",
                    })
                    st.dataframe(styled_t, use_container_width=True, hide_index=True)
                    if unmatched_count:
                        st.caption(
                            f"⚠ {unmatched_count} lot(s) have no matching open transaction "
                            "(position opened before the 12-month history window). "
                            "P&L for these is estimated from closing proceeds only — "
                            "the original premium received is unknown."
                        )

            # ── Debug ─────────────────────────────────────────────────────────
            if "portfolio_raw_debug" in st.session_state:
                with st.expander("🔍 Raw data (debugging)"):
                    import json
                    st.json(st.session_state["portfolio_raw_debug"])

        elif connected_brokers:
            st.info("Click **⟳ Load Portfolio** to fetch your positions.")
