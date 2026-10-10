"""
Local daily NLV snapshot store.

Saves one record per day per broker when the portfolio is loaded.
Accumulates over time — accurate because it uses the real API balance each day.

Storage: options_screener/portfolio/nlv_snapshots.json
Format:  {"YYYY-MM-DD": {"Broker Label": nlv_value, ...}, ...}
"""
import json
import os
from datetime import date, timedelta

_STORE_PATH = os.path.join(os.path.dirname(__file__), "nlv_snapshots.json")


def load_snapshots() -> dict:
    """Load all stored snapshots."""
    if not os.path.exists(_STORE_PATH):
        return {}
    try:
        with open(_STORE_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_today(broker_balances: dict[str, dict]):
    """
    Record today's NLV for each broker.
    broker_balances: {"Tastytrade (Individual)": {"net_liquidating_value": 142894, ...}}
    """
    today = date.today().isoformat()
    snapshots = load_snapshots()

    if today not in snapshots:
        snapshots[today] = {}

    for broker, balances in broker_balances.items():
        nlv = balances.get("net_liquidating_value")
        if nlv is not None:
            snapshots[today][broker] = round(float(nlv), 2)

    try:
        with open(_STORE_PATH, "w") as f:
            json.dump(snapshots, f, indent=2)
    except Exception:
        pass


def get_history(days: int = 365) -> dict[str, list[dict]]:
    """
    Return per-broker history for the chart.
    Returns: {"Broker Label": [{"date": "YYYY-MM-DD", "value": 142894}, ...]}
    Filtered to the last `days` calendar days, sorted ascending.
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    snapshots = load_snapshots()

    broker_history: dict[str, list[dict]] = {}

    for date_str, brokers in sorted(snapshots.items()):
        if date_str < cutoff:
            continue
        for broker, nlv in brokers.items():
            if broker not in broker_history:
                broker_history[broker] = []
            broker_history[broker].append({"date": date_str, "value": nlv, "source": "local"})

    return broker_history


def snapshot_count() -> int:
    """How many days of history we have stored."""
    return len(load_snapshots())


def merge_api_history(broker_label: str, api_records: list[dict]):
    """
    Merge API-fetched historical NLV records into the local store.
    api_records: [{"date": "YYYY-MM-DD", "value": float}, ...]
    Existing records are kept; API records fill in missing dates only.
    """
    snapshots = load_snapshots()
    for rec in api_records:
        date_str = rec.get("date", "")
        nlv = rec.get("value")
        if not date_str or nlv is None:
            continue
        if date_str not in snapshots:
            snapshots[date_str] = {}
        # Don't overwrite locally-saved values (they're from real-time API)
        if broker_label not in snapshots[date_str]:
            snapshots[date_str][broker_label] = round(float(nlv), 2)

    try:
        with open(_STORE_PATH, "w") as f:
            json.dump(snapshots, f, indent=2)
        return len(api_records)
    except Exception:
        return 0
