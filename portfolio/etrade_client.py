"""
E*TRADE API client — OAuth 1.0a.

Setup steps (one-time):
1. Go to developer.etrade.com and request API access
2. Create an app and copy Consumer Key + Consumer Secret
3. Click "Connect E*TRADE" in the sidebar — browser opens for login
4. After login, E*TRADE shows a verification code (oauth_verifier)
5. Enter that code in the app

E*TRADE uses OAuth 1.0a (3-legged):
  Step 1: Get request token → redirect user to auth URL
  Step 2: User authorizes → gets verifier code
  Step 3: Exchange request token + verifier → access token

Production base: https://api.etrade.com
Sandbox base:    https://apisb.etrade.com  (for testing)
"""
import requests
from requests_oauthlib import OAuth1Session
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PROD_BASE = "https://api.etrade.com"
REQUEST_TOKEN_URL = f"{PROD_BASE}/oauth/request_token"
AUTH_URL = "https://us.etrade.com/e/t/etws/authorize"
ACCESS_TOKEN_URL = f"{PROD_BASE}/oauth/access_token"
API_BASE = f"{PROD_BASE}/v1"


class ETradeClient:
    def __init__(self, consumer_key: str, consumer_secret: str):
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.oauth_token = None
        self.oauth_token_secret = None
        self._request_token = None
        self._request_token_secret = None
        self._session = None

    def get_auth_url(self) -> str:
        """
        Step 1: Get request token and return the URL for the user to visit.
        User will be shown a verification code after authorizing.
        """
        oauth = OAuth1Session(
            self.consumer_key,
            client_secret=self.consumer_secret,
            callback_uri="oob",  # out-of-band — user gets a code to paste
        )
        fetch_response = oauth.fetch_request_token(REQUEST_TOKEN_URL, verify=False)
        self._request_token = fetch_response.get("oauth_token")
        self._request_token_secret = fetch_response.get("oauth_token_secret")

        return f"{AUTH_URL}?key={self.consumer_key}&token={self._request_token}"

    def exchange_verifier(self, verifier: str) -> bool:
        """
        Step 2: Exchange the verifier code shown to the user for an access token.
        """
        if not self._request_token:
            raise ValueError("Call get_auth_url() first to get the request token")

        oauth = OAuth1Session(
            self.consumer_key,
            client_secret=self.consumer_secret,
            resource_owner_key=self._request_token,
            resource_owner_secret=self._request_token_secret,
            verifier=verifier,
        )
        tokens = oauth.fetch_access_token(ACCESS_TOKEN_URL, verify=False)
        self.oauth_token = tokens.get("oauth_token")
        self.oauth_token_secret = tokens.get("oauth_token_secret")

        self._session = OAuth1Session(
            self.consumer_key,
            client_secret=self.consumer_secret,
            resource_owner_key=self.oauth_token,
            resource_owner_secret=self.oauth_token_secret,
        )
        return bool(self.oauth_token)

    def is_connected(self) -> bool:
        return bool(self.oauth_token and self._session)

    def _get(self, path: str, params: dict = None) -> dict:
        r = self._session.get(
            f"{API_BASE}{path}",
            params=params,
            headers={"Accept": "application/json"},
            verify=False,
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    # ── Account Data ─────────────────────────────────────────────────────────

    def get_accounts(self) -> list[dict]:
        """Return list of accounts."""
        resp = self._get("/accounts/list")
        accounts_data = resp.get("AccountListResponse", {}).get("Accounts", {}).get("Account", [])
        if isinstance(accounts_data, dict):
            accounts_data = [accounts_data]

        accounts = []
        for acct in accounts_data:
            accounts.append({
                "account_number": acct.get("accountId"),
                "account_type": acct.get("accountType"),
                "nickname": acct.get("accountDesc") or acct.get("accountId"),
                "status": acct.get("accountStatus"),
            })
        return accounts

    def get_positions(self, account_id: str) -> list[dict]:
        """Return positions for an account."""
        resp = self._get(f"/accounts/{account_id}/portfolio")
        portfolios = resp.get("PortfolioResponse", {}).get("AccountPortfolio", [])
        if isinstance(portfolios, dict):
            portfolios = [portfolios]

        positions = []
        for portfolio in portfolios:
            positions_data = portfolio.get("Position", [])
            if isinstance(positions_data, dict):
                positions_data = [positions_data]

            for pos in positions_data:
                qty = float(pos.get("quantity", 0))
                cost_per_share = float(pos.get("costPerShare", 0))
                market_value = float(pos.get("marketValue", 0))
                total_cost = float(pos.get("totalCost", cost_per_share * abs(qty)))
                unrealized_pnl = float(pos.get("totalGain", 0))
                unrealized_pnl_pct = float(pos.get("totalGainPct", 0)) * 100

                instrument = pos.get("Product", {})
                symbol = instrument.get("symbol")
                sec_type = instrument.get("securityType", "EQ")

                positions.append({
                    "symbol": symbol,
                    "instrument_type": sec_type,
                    "quantity": qty,
                    "cost_basis": round(cost_per_share, 4),
                    "close_price": round(float(pos.get("Quick", {}).get("lastTrade", cost_per_share)), 4),
                    "market_value": round(market_value, 2),
                    "unrealized_pnl": round(unrealized_pnl, 2),
                    "unrealized_pnl_pct": round(unrealized_pnl_pct, 2),
                    "day_pnl": round(float(pos.get("todayGain", 0)), 2),
                    "multiplier": 100 if sec_type == "OPTN" else 1,
                })
        return positions

    def get_transactions(self, account_id: str, start_date: str = None) -> list[dict]:
        """Return transaction history for realized P&L."""
        from datetime import date
        if not start_date:
            today = date.today()
            start_date = f"{today.month:02d}/{today.day:02d}/{today.year - 1}"
        else:
            # Convert YYYY-MM-DD to MM/DD/YYYY for E*TRADE
            parts = start_date.split("-")
            start_date = f"{parts[1]}/{parts[2]}/{parts[0]}"

        resp = self._get(
            f"/accounts/{account_id}/transactions",
            {"startDate": start_date, "count": 250},
        )
        txn_list = resp.get("TransactionListResponse", {}).get("Transaction", [])
        if isinstance(txn_list, dict):
            txn_list = [txn_list]

        txns = []
        for item in txn_list:
            brokerage = item.get("brokerage", {})
            if brokerage.get("transactionType") not in ("Bought", "Sold", "Sold Short", "Bought to Cover"):
                continue

            product = brokerage.get("product", {})
            txns.append({
                "date": str(item.get("transactionDate", ""))[:10],
                "symbol": product.get("symbol"),
                "action": brokerage.get("transactionType"),
                "quantity": float(brokerage.get("quantity", 0)),
                "price": float(brokerage.get("price", 0)),
                "net_value": float(item.get("amount", 0)),
                "description": item.get("description"),
            })
        return txns
