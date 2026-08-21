"""Trigger a Jellyfin library refresh after a download starts.

Fladder is a Jellyfin client, so the actual "refresh the library" call goes to
the Jellyfin server. Add these to config.py:

    JELLYFIN_URL = "http://localhost:8096"
    JELLYFIN_API_KEY = "<create one in Dashboard > API Keys>"
"""
import logging
import requests

try:
    from config import JELLYFIN_URL, JELLYFIN_API_KEY
except ImportError:  # keep the bot working even if not configured yet
    JELLYFIN_URL = "http://localhost:8096"
    JELLYFIN_API_KEY = ""

log = logging.getLogger("torrentbot.fladder")


def refresh_library() -> bool:
    """Ask Jellyfin to rescan all libraries. Returns True on success.

    Note: Jellyfin only *finds* the file once the download has actually
    landed on disk. Firing this the instant a magnet is added is usually
    too early, so the bot schedules it with a short delay (see bot.py).
    """
    if not JELLYFIN_URL or not JELLYFIN_API_KEY:
        log.warning("Jellyfin not configured; skipping library refresh")
        return False
    try:
        resp = requests.post(
            f"{JELLYFIN_URL}/Library/Refresh",
            headers={"X-Emby-Token": JELLYFIN_API_KEY},
            timeout=15,
        )
        resp.raise_for_status()
        log.info("Jellyfin library refresh triggered")
        return True
    except Exception:
        log.exception("Jellyfin library refresh failed")
        return False
