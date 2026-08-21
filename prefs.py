"""Tiny JSON-backed store for per-user preferences.

Persists each Telegram user's chosen bot (UI) language and preferred film
(content/audio) language to ``data/prefs.json`` so the choices survive restarts.
The bot keeps a fast in-memory session, but reads the initial values from here
and writes back whenever the user changes something in /options or during the
first-start onboarding.
"""
import json
import logging
import os
import threading

log = logging.getLogger("torrentbot.prefs")

_PREFS_PATH = os.path.join(os.path.dirname(__file__), "data", "prefs.json")
_lock = threading.Lock()


def _load() -> dict:
    try:
        with open(_PREFS_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError):
        log.exception("failed to read %s; starting from empty prefs", _PREFS_PATH)
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(_PREFS_PATH), exist_ok=True)
    tmp = _PREFS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, _PREFS_PATH)


def get(user_id: int) -> dict:
    """Return a copy of the stored prefs for a user ({} if none)."""
    with _lock:
        return dict(_load().get(str(user_id), {}))


def update(user_id: int, **fields) -> dict:
    """Merge the given fields into a user's prefs and persist.

    ``None`` values are ignored so callers can pass only what changed. Returns
    the merged prefs for that user.
    """
    with _lock:
        data = _load()
        current = dict(data.get(str(user_id), {}))
        current.update({k: v for k, v in fields.items() if v is not None})
        data[str(user_id)] = current
        _save(data)
        return current
