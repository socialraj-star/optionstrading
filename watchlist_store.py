"""
Persists user-edited watchlists to watchlists.json so changes survive app restarts.
Falls back to defaults from config.py when the file doesn't exist.
"""
import json
import os

_STORE_PATH = os.path.join(os.path.dirname(__file__), "watchlists.json")


def load_watchlists(defaults: dict[str, list[str]]) -> dict[str, list[str]]:
    """Return saved watchlists, falling back to defaults for any missing key."""
    try:
        with open(_STORE_PATH) as f:
            saved = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        saved = {}
    return {key: saved.get(key, defaults[key]) for key in defaults}


def save_watchlist(key: str, symbols: list[str]) -> None:
    """Persist a single watchlist by key."""
    try:
        with open(_STORE_PATH) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data[key] = [s.strip().upper() for s in symbols if s.strip()]
    with open(_STORE_PATH, "w") as f:
        json.dump(data, f, indent=2)
