"""
Patches curl_cffi's Session class to disable SSL certificate verification.
Required on networks where the root CA isn't in curl_cffi's bundled Mozilla
cert store (corporate VPNs, certain Windows setups, Python 3.14).

Import this module BEFORE any yfinance import.
This is safe for a local-only screener reading public market data.
"""
try:
    from curl_cffi import requests as _curl_requests

    _OrigSession = _curl_requests.Session

    class _PatchedSession(_OrigSession):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("verify", False)
            super().__init__(*args, **kwargs)

    # Replace the Session class globally in the curl_cffi.requests namespace
    _curl_requests.Session = _PatchedSession

    # Also patch the module-level AsyncSession if present
    if hasattr(_curl_requests, "AsyncSession"):
        _OrigAsync = _curl_requests.AsyncSession

        class _PatchedAsync(_OrigAsync):
            def __init__(self, *args, **kwargs):
                kwargs.setdefault("verify", False)
                super().__init__(*args, **kwargs)

        _curl_requests.AsyncSession = _PatchedAsync

except Exception:
    pass  # curl_cffi not installed or structure changed — fail silently
