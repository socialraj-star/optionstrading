"""
Tastytrade positions fetcher.

Key field mapping (confirmed from live API):
  - quantity             → always positive from API
  - quantity-direction   → "Long" or "Short"
  - average-open-price   → per-share/contract cost basis (premium received for shorts)
  - close-price          → current mark price
  - multiplier           → 100 for options, 1 for equities
  - realized-day-gain    → today's realized gain
  - cost-effect          → "Debit"/"Credit" string — NOT a dollar amount

Transaction sign convention:
  Tastytrade always sends positive net-value; we sign by action:
  Sell to Open / Sell to Close → +  (cash received)
  Buy to Open  / Buy to Close  → -  (cash paid)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tastytrade_client import TastytradeClient
from datetime import date, timedelta

_CREDIT_ACTIONS = {"Sell to Open", "Sell to Close", "Sold Short", "Sell"}
_DEBIT_ACTIONS  = {"Buy to Open",  "Buy to Close",  "Bought to Cover", "Buy"}


# ── Accounts ──────────────────────────────────────────────────────────────────

def get_accounts(client: TastytradeClient) -> list[dict]:
    resp = client._get("/customers/me/accounts")
    accounts = []
    for item in resp.get("data", {}).get("items", []):
        acct = item.get("account", {})
        accounts.append({
            "account_number": acct.get("account-number"),
            "nickname":       acct.get("nickname") or acct.get("account-number"),
            "account_type":   acct.get("account-type-name"),
        })
    return accounts


# ── Positions ─────────────────────────────────────────────────────────────────

def get_positions(client: TastytradeClient, account_number: str) -> list[dict]:
    resp = client._get(f"/accounts/{account_number}/positions")
    positions = []

    for item in resp.get("data", {}).get("items", []):
        raw_qty   = abs(_to_float(item.get("quantity")) or 0.0)
        direction = item.get("quantity-direction", "Long")
        is_short  = direction == "Short"
        signed_qty = -raw_qty if is_short else raw_qty

        avg_open    = _to_float(item.get("average-open-price")) or 0.0
        close_price = _to_float(item.get("close-price")) or 0.0
        multiplier  = int(_to_float(item.get("multiplier")) or 1)

        # Market value: negative for short (liability)
        market_value = close_price * signed_qty * multiplier

        # Unrealized P&L
        if avg_open:
            if is_short:
                unrealized_pnl     = (avg_open - close_price) * raw_qty * multiplier
                unrealized_pnl_pct = (avg_open - close_price) / avg_open * 100
            else:
                unrealized_pnl     = (close_price - avg_open) * raw_qty * multiplier
                unrealized_pnl_pct = (close_price - avg_open) / avg_open * 100
        else:
            unrealized_pnl, unrealized_pnl_pct = 0.0, None

        positions.append({
            "symbol":             item.get("symbol"),
            "underlying":         item.get("underlying-symbol") or item.get("symbol"),
            "instrument_type":    item.get("instrument-type", "Equity"),
            "direction":          direction,
            "quantity":           signed_qty,
            "cost_basis":         avg_open,
            "close_price":        close_price,
            "market_value":       round(market_value, 2),
            "unrealized_pnl":     round(unrealized_pnl, 2),
            "unrealized_pnl_pct": round(unrealized_pnl_pct, 2) if unrealized_pnl_pct is not None else None,
            "day_pnl":            _to_float(item.get("realized-day-gain")) or 0.0,
            "multiplier":         multiplier,
        })

    return positions


# ── Balances ──────────────────────────────────────────────────────────────────

def get_balances(client: TastytradeClient, account_number: str) -> dict:
    resp = client._get(f"/accounts/{account_number}/balances")
    data = resp.get("data", {})
    return {
        "net_liquidating_value":    _to_float(data.get("net-liquidating-value")),
        "equity_buying_power":      _to_float(data.get("equity-buying-power")),
        "derivative_buying_power":  _to_float(data.get("derivative-buying-power")),
        "crypto_buying_power":      _to_float(data.get("cryptocurrency-buying-power")),
        "cash":                     _to_float(data.get("cash-balance")),
        # raw field dump so callers can inspect what's available
        "_raw_keys":                list(data.keys()),
    }


# ── Today's Trades Count ──────────────────────────────────────────────────────

def get_todays_trade_count(client: TastytradeClient, account_number: str) -> int:
    """Return number of trade executions today (fills only)."""
    today = date.today().isoformat()
    try:
        resp = client._get(
            f"/accounts/{account_number}/transactions",
            params={"start-date": today, "per-page": 250},
        )
        items = resp.get("data", {}).get("items", [])
        return sum(1 for i in items if i.get("transaction-type") in ("Trade", "Receive Deliver"))
    except Exception:
        return 0


# ── Historical NLV Backfill ───────────────────────────────────────────────────

def backfill_nlv_history(client: TastytradeClient, account_number: str,
                         days: int = 365) -> list[dict]:
    """
    Fetch end-of-day NLV for each past trading day via balance-snapshots.
    Makes one API call per day — use sparingly (run once to seed the chart).
    Returns list of {"date": "YYYY-MM-DD", "value": float}.
    """
    from datetime import timedelta
    import time

    today = date.today()
    results = []

    for i in range(1, days + 1):
        d = (today - timedelta(days=i))
        if d.weekday() >= 5:           # skip weekends
            continue
        date_str = d.isoformat()
        for time_of_day in ("EOD", "eod"):
            try:
                resp = client._get(
                    f"/accounts/{account_number}/balance-snapshots",
                    params={"snapshot-date": date_str, "time-of-day": time_of_day},
                )
                items = resp.get("data", {}).get("items", [])
                for item in items:
                    nlv = _to_float(item.get("net-liquidating-value"))
                    if nlv is not None:
                        results.append({"date": date_str, "value": nlv})
                        break
                if results and results[-1]["date"] == date_str:
                    break   # found for this day
            except Exception:
                pass

    return sorted(results, key=lambda x: x["date"])


# ── Transactions (all pages) ──────────────────────────────────────────────────

def get_transactions(client: TastytradeClient, account_number: str,
                     start_date: str = None) -> list[dict]:
    """Fetch ALL transaction pages for accurate P&L."""
    if not start_date:
        today = date.today()
        start_date = (today - timedelta(days=365)).isoformat()

    all_txns = []
    page = 0
    per_page = 250

    while True:
        params = {"start-date": start_date, "per-page": per_page, "page-offset": page}
        resp = client._get(f"/accounts/{account_number}/transactions", params=params)
        data = resp.get("data", {})
        items = data.get("items", [])

        for item in items:
            txn_type = item.get("transaction-type", "")
            if txn_type not in ("Trade", "Receive Deliver"):
                continue

            action  = item.get("action", "")
            raw_val = abs(_to_float(item.get("net-value")) or 0.0)

            if action in _CREDIT_ACTIONS:
                net_value = +raw_val
            elif action in _DEBIT_ACTIONS:
                net_value = -raw_val
            else:
                net_value = raw_val

            all_txns.append({
                "date":            str(item.get("executed-at", ""))[:10],
                "symbol":          item.get("symbol"),
                "instrument_type": item.get("instrument-type"),
                "action":          action,
                "quantity":        _to_float(item.get("quantity")),
                "price":           _to_float(item.get("price")),
                "net_value":       net_value,
                "description":     item.get("description"),
            })

        # Check if there are more pages
        pagination = data.get("pagination", {})
        total = pagination.get("total-items") or data.get("total-items")
        if total and (page + 1) * per_page >= int(total):
            break
        if len(items) < per_page:
            break
        page += 1

    return all_txns


# ── Net Liquidating Value History ─────────────────────────────────────────────

def get_net_liq_history(client: TastytradeClient, account_number: str) -> tuple[list[dict], str]:
    """
    Fetch daily EOD net-liquidating-value history for the past 12 months.
    Uses Tastytrade's /balance-snapshots endpoint.

    Note: snapshot-date filters to a SPECIFIC date; to get a range we fetch
    all EOD snapshots and filter client-side.
    """
    cutoff = (date.today() - timedelta(days=365)).isoformat()

    # Fetch all EOD snapshots (no date filter — get everything, filter client-side)
    for time_of_day in ("EOD", "eod", "EoD"):
        try:
            resp = client._get(
                f"/accounts/{account_number}/balance-snapshots",
                params={"time-of-day": time_of_day},
            )
            items = resp.get("data", {}).get("items", [])
            if not items:
                continue

            result = []
            for item in items:
                snapshot_date = str(item.get("snapshot-date", ""))[:10]
                nlv = _to_float(item.get("net-liquidating-value"))
                if snapshot_date and nlv is not None and snapshot_date >= cutoff:
                    result.append({"date": snapshot_date, "value": nlv, "source": "api"})

            if result:
                return sorted(result, key=lambda x: x["date"]), f"api ({len(result)} snapshots, time-of-day={time_of_day})"

        except Exception as e:
            last_err = str(e)
            continue

    # If EOD returns nothing, try BOD
    for time_of_day in ("BOD", "bod"):
        try:
            resp = client._get(
                f"/accounts/{account_number}/balance-snapshots",
                params={"time-of-day": time_of_day},
            )
            items = resp.get("data", {}).get("items", [])
            if not items:
                continue

            result = []
            for item in items:
                snapshot_date = str(item.get("snapshot-date", ""))[:10]
                nlv = _to_float(item.get("net-liquidating-value"))
                if snapshot_date and nlv is not None and snapshot_date >= cutoff:
                    result.append({"date": snapshot_date, "value": nlv, "source": "api"})

            if result:
                return sorted(result, key=lambda x: x["date"]), f"api-BOD ({len(result)} snapshots)"

        except Exception as e:
            last_err = str(e)
            continue

    # Last resort: raw call with no params — inspect what comes back
    try:
        resp = client._get(f"/accounts/{account_number}/balance-snapshots")
        items = resp.get("data", {}).get("items", [])
        if items:
            # Log the first item's keys so we can adapt
            first_keys = list(items[0].keys())
            return [], f"endpoint exists but field names unexpected: {first_keys} (got {len(items)} items)"
    except Exception as e:
        pass

    return [], f"balance-snapshots unavailable: {locals().get('last_err', 'unknown error')}"


def _reconstruct_net_liq(client: TastytradeClient, account_number: str) -> list[dict]:
    """
    Approximate daily NLV by walking backwards from today using matched realized P&L.

    Key insight: trade cash flows (sell $20k of LEAPS → receive $20k cash) do NOT
    change NLV because the position value was already counted. Only the P&L portion
    of a closed trade actually changes NLV. Using full cash flows causes double-counting.

    This reconstruction is still approximate (ignores daily M2M moves on open positions)
    but is far more accurate than using raw cash flows.
    """
    try:
        balances = get_balances(client, account_number)
        current_nlv = balances.get("net_liquidating_value") or 0.0
    except Exception:
        return []

    today = date.today()
    start = today - timedelta(days=365)
    txns = get_transactions(client, account_number, start_date=start.isoformat())

    # Use matched realized P&L (gain/loss only — not full trade value)
    from portfolio.pnl_calculator import match_realized_pnl
    trades = match_realized_pnl(txns)

    daily_pnl: dict[str, float] = {}
    for trade in trades:
        d = trade.get("date", "")
        if d:
            daily_pnl[d] = daily_pnl.get(d, 0.0) + trade["pnl"]

    # Walk backwards: subtract realized P&L to get prior-day approximate NLV
    result = []
    running = current_nlv
    for i in range(365):
        d = (today - timedelta(days=i)).isoformat()
        result.append({"date": d, "value": round(running, 2)})
        if d in daily_pnl:
            running -= daily_pnl[d]

    return sorted(result, key=lambda x: x["date"])


def _to_float(val) -> float | None:
    if val is None:
        return None
    try:
        return round(float(val), 4)
    except (ValueError, TypeError):
        return None
