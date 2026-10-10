"""
P&L calculation engine.

Realized P&L approach:
  Each transaction's net_value is already signed:
    Sell to Open / Sell to Close → positive (cash received)
    Buy to Open  / Buy to Close  → negative (cash paid)

  Monthly/yearly P&L = sum of net_values in that period.
  This is cash-flow based (not matched-lot), which is accurate for
  options traders who open and close positions within the same period.
  Cross-period trades (e.g. LEAPS bought last year, sold this month)
  will show only the closing leg in this period — flagged in the UI.
"""
import pandas as pd
from datetime import date, timedelta


def aggregate_positions(broker_positions: dict[str, list[dict]]) -> pd.DataFrame:
    rows = []
    for broker, positions in broker_positions.items():
        for pos in positions:
            rows.append({**pos, "broker": broker})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["unrealized_pnl"] = pd.to_numeric(df.get("unrealized_pnl", 0), errors="coerce").fillna(0)
    df["market_value"]   = pd.to_numeric(df.get("market_value", 0), errors="coerce").fillna(0)
    df["day_pnl"]        = pd.to_numeric(df.get("day_pnl", 0), errors="coerce").fillna(0)
    return df


def portfolio_summary(positions_df: pd.DataFrame) -> dict:
    if positions_df.empty:
        return {}
    return {
        "total_market_value": positions_df["market_value"].sum(),
        "total_unrealized_pnl": positions_df["unrealized_pnl"].sum(),
        "total_day_pnl": positions_df["day_pnl"].sum(),
        "by_broker": positions_df.groupby("broker")["market_value"].apply(lambda x: abs(x).sum()).to_dict(),
        "by_type":   positions_df.groupby("instrument_type")["market_value"].apply(lambda x: abs(x).sum()).to_dict(),
        "position_count": len(positions_df),
        "winners": int((positions_df["unrealized_pnl"] > 0).sum()),
        "losers":  int((positions_df["unrealized_pnl"] < 0).sum()),
    }


def match_realized_pnl(transactions: list[dict]) -> list[dict]:
    """
    Quantity-aware FIFO matching of opening and closing transactions.

    Each open lot is stored with its remaining quantity and per-unit price.
    A close transaction consumes lots in order, splitting across multiple opens
    when the close quantity exceeds a single lot.

    Rules:
      Long  (Buy to Open  → Sell to Close): P&L = (close_price − open_price) × qty
      Short (Sell to Open → Buy to Close):  P&L = (open_price  − close_price) × qty
      Unmatched close (open outside 12-month window):
        Sell to Close → P&L = +close proceeds  (upper-bound estimate)
        Buy to Close  → P&L = −close cost      (lower-bound estimate, likely understated)
    """
    _OPEN_ACTIONS  = {"Buy to Open", "Sell to Open"}
    _CLOSE_ACTIONS = {"Sell to Close", "Buy to Close"}

    # opens[symbol] = list of {rem_qty, price_per_unit, action, date}
    opens: dict[str, list[dict]] = {}
    realized: list[dict] = []

    def _qty(txn):
        v = txn.get("quantity")
        try:
            return abs(float(v)) if v is not None else 1.0
        except (TypeError, ValueError):
            return 1.0

    for txn in sorted(transactions, key=lambda x: x.get("date", "")):
        action = txn.get("action", "")
        symbol = txn.get("symbol", "")
        qty    = _qty(txn)
        net    = abs(float(txn.get("net_value", 0) or 0))
        ppu    = net / qty if qty else 0.0          # price per unit (contract/share)

        if action in _OPEN_ACTIONS:
            opens.setdefault(symbol, []).append({
                "rem_qty":       qty,
                "price_per_unit": ppu,
                "action":        action,
                "date":          txn.get("date"),
            })

        elif action in _CLOSE_ACTIONS:
            queue          = opens.setdefault(symbol, [])
            close_remaining = qty

            while close_remaining > 0.001 and queue:
                lot          = queue[0]
                matched_qty  = min(close_remaining, lot["rem_qty"])
                open_cost    = lot["price_per_unit"] * matched_qty
                close_proc   = ppu * matched_qty

                if action == "Sell to Close":
                    pnl = close_proc - open_cost          # long: proceeds minus cost
                else:
                    pnl = open_cost - close_proc          # short: premium minus close cost

                realized.append({
                    "date":         txn["date"],
                    "symbol":       symbol,
                    "quantity":     round(matched_qty, 4),
                    "pnl":          round(pnl, 2),
                    "close_action": action,
                    "open_date":    lot["date"],
                    "open_action":  lot["action"],
                    "matched":      True,
                })

                lot["rem_qty"]  -= matched_qty
                close_remaining -= matched_qty
                if lot["rem_qty"] < 0.001:
                    queue.pop(0)

            # Any residual has no matching open (opened before history window)
            if close_remaining > 0.001:
                unmatched_net = ppu * close_remaining
                pnl = unmatched_net if action == "Sell to Close" else -unmatched_net
                realized.append({
                    "date":         txn["date"],
                    "symbol":       symbol,
                    "quantity":     round(close_remaining, 4),
                    "pnl":          round(pnl, 2),
                    "close_action": action,
                    "open_date":    None,
                    "open_action":  None,
                    "matched":      False,
                    "note":         "Open outside 12-month history window",
                })

    return realized


def realized_pnl_by_period(transactions: list[dict]) -> dict:
    """
    Compute realized P&L using matched open/close pairs.
    Returns daily/monthly/quarterly/yearly dicts + all_time total.
    """
    if not transactions:
        return {"daily": {}, "monthly": {}, "quarterly": {}, "yearly": {}, "all_time": 0.0,
                "trades": []}

    trades = match_realized_pnl(transactions)
    if not trades:
        return {"daily": {}, "monthly": {}, "quarterly": {}, "yearly": {}, "all_time": 0.0,
                "trades": []}

    df = pd.DataFrame(trades)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    df["pnl"]  = pd.to_numeric(df["pnl"], errors="coerce").fillna(0)

    daily     = df.groupby(df["date"].dt.date)["pnl"].sum()
    df["month"]   = df["date"].dt.to_period("M").astype(str)
    monthly   = df.groupby("month")["pnl"].sum()
    df["quarter"] = df["date"].dt.to_period("Q").astype(str)
    quarterly = df.groupby("quarter")["pnl"].sum()
    df["year"]    = df["date"].dt.year.astype(str)
    yearly    = df.groupby("year")["pnl"].sum()

    return {
        "daily":     {str(k): round(v, 2) for k, v in daily.items()},
        "monthly":   {k: round(v, 2) for k, v in monthly.items()},
        "quarterly": {k: round(v, 2) for k, v in quarterly.items()},
        "yearly":    {k: round(v, 2) for k, v in yearly.items()},
        "all_time":  round(df["pnl"].sum(), 2),
        "trades":    trades,
    }


def pnl_period_summary(realized_pnl: dict,
                       positions_df,          # pd.DataFrame of current open positions
                       transactions: list[dict],
                       portfolio_nlv: float = 0.0) -> list[dict]:
    """
    Summary table rows for each time period.

    Realized P&L   — matched open/close lots whose closing date falls in the period.
    Unrealized P&L — mark-to-market on positions whose OPEN date falls in the period.
                     "All Time (12m)" row shows total unrealized across all open positions.
    """
    import pandas as _pd

    today      = date.today()
    trades     = realized_pnl.get("trades", [])

    # ── Realized: sum matched closes within period ────────────────────────────
    def _realized(since: date, until: date) -> tuple[float, int]:
        total, count = 0.0, 0
        for t in trades:
            try:
                d = date.fromisoformat(str(t.get("date", ""))[:10])
            except Exception:
                continue
            if since <= d <= until:
                total += t.get("pnl", 0)
                count += 1
        return round(total, 2), count

    # ── Build symbol → earliest open date from transaction history ────────────
    _OPEN_ACTIONS = {"Buy to Open", "Sell to Open"}
    symbol_open_date: dict[str, date] = {}
    for txn in transactions:
        if txn.get("action") in _OPEN_ACTIONS:
            sym = txn.get("symbol", "")
            raw = str(txn.get("date", ""))[:10]
            try:
                d = date.fromisoformat(raw)
            except Exception:
                continue
            if sym and (sym not in symbol_open_date or d < symbol_open_date[sym]):
                symbol_open_date[sym] = d

    # ── Unrealized: sum open positions whose open date is within period ───────
    def _unrealized(since: date, until: date, all_positions: bool = False) -> tuple[float, float]:
        if positions_df is None or (hasattr(positions_df, "empty") and positions_df.empty):
            return 0.0, 0.0
        total = 0.0
        for _, row in positions_df.iterrows():
            upnl = float(row.get("unrealized_pnl", 0) or 0)
            if all_positions:
                total += upnl
                continue
            sym = row.get("symbol", "")
            open_d = symbol_open_date.get(sym)
            if open_d is None:
                continue          # opened before transaction history — exclude from sub-periods
            if since <= open_d <= until:
                total += upnl
        pct = (total / portfolio_nlv * 100) if portfolio_nlv else 0.0
        return round(total, 2), round(pct, 2)

    # ── Period definitions ────────────────────────────────────────────────────
    q_month    = ((today.month - 1) // 3) * 3 + 1
    week_start = today - timedelta(days=today.weekday())

    periods = [
        ("Today",         today,                               today,  False),
        ("This Week",     week_start,                          today,  False),
        ("MTD",           today.replace(day=1),                today,  False),
        ("This Quarter",  today.replace(month=q_month, day=1), today,  False),
        ("YTD",           today.replace(month=1, day=1),       today,  False),
        ("All Time (12m)",today - timedelta(days=365),         today,  True),
    ]

    rows = []
    for label, since, until, all_pos in periods:
        rpnl, cnt   = _realized(since, until)
        upnl, upct  = _unrealized(since, until, all_positions=all_pos)
        rows.append({
            "Period":         label,
            "Realized P&L":   rpnl,
            "Closed Trades":  cnt,
            "Unrealized P&L": upnl,
            "Unrealized %":   upct if upct != 0.0 or upnl != 0.0 else None,
        })

    return rows


def build_net_liq_chart_data(
    broker_histories: dict[str, list[dict]]
) -> pd.DataFrame:
    """
    Combine per-broker net-liq histories into a daily DataFrame.
    broker_histories: {"Tastytrade (Ind)": [{"date": "2025-08-04", "value": 142894}], ...}
    Returns DataFrame with columns: date, broker, value, total
    """
    frames = []
    for broker, history in broker_histories.items():
        if not history:
            continue
        df = pd.DataFrame(history)
        df["date"]   = pd.to_datetime(df["date"])
        df["broker"] = broker
        df["value"]  = pd.to_numeric(df["value"], errors="coerce")
        frames.append(df)

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)

    # Build daily total across all brokers
    pivot = combined.pivot_table(index="date", columns="broker", values="value", aggfunc="sum")
    pivot = pivot.interpolate(method="time").ffill().fillna(0)
    pivot["Total"] = pivot.sum(axis=1)
    pivot = pivot.reset_index()

    return pivot
