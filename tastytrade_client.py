"""
Lightweight Tastytrade REST client.
Uses the /sessions endpoint for username/password auth (no OAuth needed).
Provides IV Rank and market metrics — the key data yfinance can't give us.
"""
import requests
import urllib3
from datetime import date

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://api.tastytrade.com"

_HEADERS_BASE = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "tastytrade-python-client/1.0",
}

# ── Windows Firewall / SSL bypass ─────────────────────────────────────────────
# On Windows, Python's ssl module can be blocked at the kernel level (WFP),
# causing PermissionError(13) even with verify=False.
# curl_cffi ships its own libcurl DLL that uses a different socket path and
# avoids the Windows Filtering Platform restriction.

try:
    from curl_cffi import requests as _curl
    _CURL_AVAILABLE = True
except ImportError:
    _CURL_AVAILABLE = False

def _is_network_error(e: Exception) -> bool:
    s = str(e)
    return (
        "PermissionError" in s
        or "SSL" in s
        or "CERTIFICATE" in s.upper()
        or "Connection aborted" in s
        or "ConnectionError" in type(e).__name__
    )

def _post(url, **kwargs):
    """
    POST with multi-layer Windows Firewall fallback:
      1. curl_cffi (libcurl, bypasses WFP)            — preferred
      2. requests verify=True
      3. requests verify=False
    """
    if _CURL_AVAILABLE:
        try:
            return _curl.post(url, verify=False, **kwargs)
        except Exception:
            pass  # fall through to requests
    try:
        return requests.post(url, verify=True, **kwargs)
    except Exception as e:
        if _is_network_error(e):
            return requests.post(url, verify=False, **kwargs)
        raise

def _get_req(url, **kwargs):
    """
    GET with multi-layer Windows Firewall fallback:
      1. curl_cffi (libcurl, bypasses WFP)            — preferred
      2. requests verify=True
      3. requests verify=False
    """
    if _CURL_AVAILABLE:
        try:
            return _curl.get(url, verify=False, **kwargs)
        except Exception:
            pass  # fall through to requests
    try:
        return requests.get(url, verify=True, **kwargs)
    except Exception as e:
        if _is_network_error(e):
            return requests.get(url, verify=False, **kwargs)
        raise


class DeviceChallengeRequired(Exception):
    """Raised when Tastytrade requires device verification."""
    def __init__(self, challenge_token: str):
        self.challenge_token = challenge_token
        super().__init__("Device verification required")


def trigger_device_challenge(challenge_token: str) -> bool:
    """POST to /device-challenge to send verification code to user's email/phone."""
    r = _post(
        f"{BASE_URL}/device-challenge",
        headers={**_HEADERS_BASE, "X-Tastyworks-Challenge-Token": challenge_token},
        json={},
        timeout=15,
    )
    return r.ok


class TastytradeClient:
    def __init__(self, username: str, password: str, otp: str = None, challenge_token: str = None):
        self.username = username
        self.session_token = None
        self._login(username, password, otp=otp, challenge_token=challenge_token)

    # ── Auth ─────────────────────────────────────────────────────────────────

    def _login(self, username: str, password: str, otp: str = None, challenge_token: str = None):
        headers = {**_HEADERS_BASE}
        if otp:
            headers["X-Tastyworks-OTP"] = otp.strip()
        if challenge_token:
            headers["X-Tastyworks-Challenge-Token"] = challenge_token.strip()

        r = _post(
            f"{BASE_URL}/sessions",
            json={"login": username, "password": password, "remember-me": True},
            headers=headers,
            timeout=15,
        )

        if not r.ok:
            try:
                body = r.json()
                error = body.get("error", {})
                code = error.get("code", "")
                msg = error.get("message", r.text)
            except Exception:
                code, msg = "", r.text or r.reason

            if code == "device_challenge_required":
                token = r.headers.get("X-Tastyworks-Challenge-Token", "")
                raise DeviceChallengeRequired(token)

            raise Exception(f"[{r.status_code}] {msg}")

        self.session_token = r.json()["data"]["session-token"]

    # ── HTTP ─────────────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict = None) -> dict:
        r = _get_req(
            f"{BASE_URL}{path}",
            params=params,
            headers={**_HEADERS_BASE, "Authorization": self.session_token},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    # ── Session ───────────────────────────────────────────────────────────────

    def is_valid(self) -> bool:
        try:
            self._get("/sessions/validate")
            return True
        except Exception:
            return False

    # ── Market Metrics (IV Rank) ──────────────────────────────────────────────

    def get_market_metrics(self, symbols: list[str]) -> dict[str, dict]:
        """
        Returns: {symbol: {iv_rank, iv_pct, iv, iv_5d_change, liquidity_rank, beta, corr_spy}}
        """
        resp = self._get("/market-metrics", {"symbols": ",".join(symbols)})
        result = {}
        for item in resp.get("data", {}).get("items", []):
            sym = item.get("symbol", "")
            result[sym] = {
                "iv_rank":               _pct(item.get("implied-volatility-index-rank")),
                "iv_pct":                _pct(item.get("implied-volatility-percentile")),
                "iv":                    _pct(item.get("implied-volatility-index")),
                "iv_5d_change":          _to_float(item.get("implied-volatility-index-5-day-change")),
                "liquidity_rank":        _to_float(item.get("liquidity-rating")),
                "earnings_expected_move":_to_float(item.get("earnings-expected-move")),
                "beta":                  _to_float(item.get("beta")),
                "corr_spy":              _to_float(item.get("corr-spy-3month")),
                "dividend_yield":        _pct(item.get("dividend-yield")),
            }
        return result

    # ── Options Chain Structure ───────────────────────────────────────────────

    def get_option_chain(self, symbol: str, min_dte: int = 0, max_dte: int = 999) -> list[dict]:
        resp = self._get(f"/option-chains/{symbol}/nested")
        items = resp.get("data", {}).get("items", [])
        result = []
        today = date.today()
        for chain in items:
            for exp in chain.get("expirations", []):
                exp_date_str = exp.get("expiration-date", "")
                try:
                    exp_date = date.fromisoformat(exp_date_str)
                except ValueError:
                    continue
                dte = (exp_date - today).days
                if not (min_dte <= dte <= max_dte):
                    continue
                for strike in exp.get("strikes", []):
                    result.append({
                        "expiry":   exp_date_str,
                        "dte":      dte,
                        "strike":   float(strike.get("strike-price", 0)),
                        "call_occ": strike.get("call"),
                        "put_occ":  strike.get("put"),
                    })
        return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_float(val) -> float | None:
    if val is None:
        return None
    try:
        return round(float(val), 3)
    except (ValueError, TypeError):
        return None


def _pct(val) -> float | None:
    """Convert decimal (0.45) to percentage (45.0)."""
    if val is None:
        return None
    try:
        return round(float(val) * 100, 1)
    except (ValueError, TypeError):
        return None
