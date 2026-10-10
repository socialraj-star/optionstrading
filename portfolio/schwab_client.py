"""
Charles Schwab Trader API client — OAuth 2.0.

One-time setup:
  1. Go to https://developer.schwab.com and create an app
  2. Set callback URL to https://127.0.0.1
  3. Copy Client ID and Client Secret into the sidebar
  4. Click "Get Auth URL" → browser opens → login → authorise
  5. Schwab redirects to https://127.0.0.1?code=...  (page won't load — that's fine)
  6. Copy the full redirect URL and paste it into the app

API ref: https://developer.schwab.com/products/trader-api--individual-
"""
import requests
import urllib.parse
import base64
import time
import urllib3
from datetime import date, timedelta

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

AUTH_URL  = "https://api.schwabapi.com/v1/oauth/authorize"
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
BASE_URL  = "https://api.schwabapi.com/trader/v1"

# ── Schwab action → standard action ──────────────────────────────────────────
# Schwab transaction transferItems carry positionEffect = OPENING / CLOSING
# and instruction = BUY / SELL (derived from sign of amount or description).
_ACTION_MAP = {
    ("BUY",  "OPENING"): "Buy to Open",
    ("BUY",  "CLOSING"): "Buy to Close",
    ("SELL", "OPENING"): "Sell to Open",
    ("SELL", "CLOSING"): "Sell to Close",
    # fallbacks when positionEffect absent
    ("BUY",  None):      "Buy to Open",
    ("SELL", None):      "Sell to Open",
}


class SchwabClient:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id     = client_id
        self.client_secret = client_secret
        self.access_token  = None
        self.refresh_token = None
        self.token_expiry  = 0.0

    # ── OAuth ─────────────────────────────────────────────────────────────────

    def get_auth_url(self) -> str:
        params = {
            "client_id":     self.client_id,
            "redirect_uri":  "https://127.0.0.1",
            "response_type": "code",
            "scope":         "readonly",
        }
        return AUTH_URL + "?" + urllib.parse.urlencode(params)

    def exchange_code(self, redirect_url: str) -> bool:
        """Exchange auth code from redirect URL for access + refresh tokens."""
        parsed = urllib.parse.urlparse(redirect_url)
        params = urllib.parse.parse_qs(parsed.query)
        code   = params.get("code", [None])[0]
        if not code:
            raise ValueError("No authorization code found in redirect URL")

        r = self._token_request({
            "grant_type":   "authorization_code",
            "code":          code,
            "redirect_uri":  "https://127.0.0.1",
        })
        r.raise_for_status()
        return self._store_tokens(r.json())

    def _refresh_access_token(self) -> bool:
        if not self.refresh_token:
            return False
        r = self._token_request({
            "grant_type":    "refresh_token",
            "refresh_token":  self.refresh_token,
        })
        if not r.ok:
            return False
        return self._store_tokens(r.json())

    def _token_request(self, data: dict) -> requests.Response:
        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()
        return requests.post(
            TOKEN_URL,
            data=data,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type":  "application/x-www-form-urlencoded",
            },
            verify=False,
            timeout=15,
        )

    def _store_tokens(self, data: dict) -> bool:
        self.access_token  = data.get("access_token")
        self.refresh_token = data.get("refresh_token", self.refresh_token)
        expires_in         = data.get("expires_in", 1800)
        self.token_expiry  = time.time() + expires_in - 60
        return bool(self.access_token)

    def _ensure_token(self):
        if time.time() >= self.token_expiry:
            self._refresh_access_token()

    def is_connected(self) -> bool:
        return bool(self.access_token) and (
            time.time() < self.token_expiry or bool(self.refresh_token)
        )

    # ── HTTP ─────────────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict = None) -> dict | list:
        self._ensure_token()
        r = requests.get(
            f"{BASE_URL}{path}",
            params=params,
            headers={"Authorization": f"Bearer {self.access_token}"},
            verify=False,
            timeout=20,
        )
        r.raise_for_status()
        return r.json()

    # ── Accounts & Balances ───────────────────────────────────────────────────

    def get_accounts(self) -> list[dict]:
        """Return list of accounts. Includes balances needed for portfolio view."""
        resp = self._get("/accounts", {"fields": "positions"})
        accounts = []
        for item in (resp if isinstance(resp, list) else []):
            acct     = item.get("securitiesAccount", {})
            balances = acct.get("currentBalances", {})
            accounts.append({
                "account_number":        acct.get("accountNumber"),
                "account_type":          acct.get("type"),
                "nickname":              acct.get("accountNumber"),
                # Mirror the Tastytrade balance field names
                "net_liquidating_value": _f(balances.get("liquidationValue")),
                "cash":                  _f(balances.get("cashBalance")),
                "equity_buying_power":   _f(balances.get("buyingPower")),
                "derivative_buying_power": _f(
                    balances.get("optionBuyingPower")
                    or balances.get("buyingPower")
                ),
            })
        return accounts

    # ── Positions ─────────────────────────────────────────────────────────────

    def get_positions(self, account_number: str) -> list[dict]:
        resp     = self._get(f"/accounts/{account_number}", {"fields": "positions"})
        acct     = resp.get("securitiesAccount", {})
        raw_list = acct.get("positions", [])

        positions = []
        for pos in raw_list:
            instrument = pos.get("instrument", {})
            asset_type = instrument.get("assetType", "EQUITY")
            multiplier = 100 if asset_type == "OPTION" else 1

            long_qty  = _f(pos.get("longQuantity"))  or 0.0
            short_qty = _f(pos.get("shortQuantity")) or 0.0
            # signed: positive = long, negative = short
            signed_qty = long_qty - short_qty
            is_short   = signed_qty < 0
            raw_qty    = abs(signed_qty)

            avg_price    = _f(pos.get("averagePrice")) or 0.0
            market_value = _f(pos.get("marketValue"))  or 0.0

            # Derive current mark price from market value
            denom      = raw_qty * multiplier
            close_price = round(market_value / denom, 4) if denom else avg_price

            # Unrealized P&L — match Tastytrade convention
            if avg_price:
                if is_short:
                    # Short: we received avg_price, mark is close_price
                    unrealized_pnl = (avg_price - close_price) * raw_qty * multiplier
                else:
                    unrealized_pnl = (close_price - avg_price) * raw_qty * multiplier
                unrealized_pct = unrealized_pnl / (avg_price * raw_qty * multiplier) * 100
            else:
                unrealized_pnl, unrealized_pct = 0.0, 0.0

            positions.append({
                "symbol":             instrument.get("symbol"),
                "underlying":         instrument.get("underlyingSymbol") or instrument.get("symbol"),
                "instrument_type":    asset_type,
                "direction":          "Short" if is_short else "Long",
                "quantity":           signed_qty,
                "cost_basis":         round(avg_price, 4),
                "close_price":        close_price,
                "market_value":       round(market_value, 2),
                "unrealized_pnl":     round(unrealized_pnl, 2),
                "unrealized_pnl_pct": round(unrealized_pct, 2),
                "day_pnl":            round(_f(pos.get("currentDayProfitLoss")) or 0.0, 2),
                "multiplier":         multiplier,
            })
        return positions

    # ── Transactions ─────────────────────────────────────────────────────────

    def get_transactions(self, account_number: str,
                         start_date: str = None) -> list[dict]:
        """
        Fetch 12 months of TRADE transactions and map to the standard format
        used by the P&L calculator (same action labels as Tastytrade).
        """
        if not start_date:
            start_date = (date.today() - timedelta(days=365)).isoformat()
        end_date = date.today().isoformat()

        raw = self._get(
            f"/accounts/{account_number}/transactions",
            {"startDate": start_date, "endDate": end_date, "types": "TRADE"},
        )

        txns = []
        for item in (raw if isinstance(raw, list) else []):
            transfer_items = item.get("transferItems", [])
            if not transfer_items:
                continue

            # Primary leg (index 0 is usually the security, index 1 is cash)
            leg = transfer_items[0]
            instrument = leg.get("instrument", {})

            # Determine BUY / SELL from amount sign or description
            amount = _f(leg.get("amount")) or 0.0
            desc   = (item.get("description") or "").upper()

            if amount > 0:
                side = "BUY"
            elif amount < 0:
                side = "SELL"
            elif "BOUGHT" in desc or "BUY" in desc:
                side = "BUY"
            elif "SOLD" in desc or "SELL" in desc:
                side = "SELL"
            else:
                continue   # can't determine side — skip

            pos_effect = (leg.get("positionEffect") or "").upper() or None
            action     = _ACTION_MAP.get((side, pos_effect)) or _ACTION_MAP.get((side, None))
            if not action:
                continue

            # net_value: positive for credits (sell), negative for debits (buy)
            net_raw = _f(item.get("netAmount")) or 0.0
            if side == "SELL":
                net_value = abs(net_raw)
            else:
                net_value = -abs(net_raw)

            txns.append({
                "date":            str(item.get("tradeDate", ""))[:10],
                "symbol":          instrument.get("symbol"),
                "instrument_type": instrument.get("assetType", "EQUITY"),
                "action":          action,
                "quantity":        abs(amount),
                "price":           _f(leg.get("price")),
                "net_value":       round(net_value, 4),
                "description":     item.get("description"),
            })
        return txns

    def get_todays_trade_count(self, account_number: str) -> int:
        """Count trade executions today."""
        today = date.today().isoformat()
        try:
            raw = self._get(
                f"/accounts/{account_number}/transactions",
                {"startDate": today, "endDate": today, "types": "TRADE"},
            )
            return len(raw) if isinstance(raw, list) else 0
        except Exception:
            return 0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _f(val) -> float | None:
    if val is None:
        return None
    try:
        return round(float(val), 4)
    except (TypeError, ValueError):
        return None
