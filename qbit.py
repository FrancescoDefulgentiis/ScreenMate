"""qBittorrent Web API client.

The bot never touches the Jellyfin folders directly: it hands magnets to a
qBittorrent instance (which can live on any host reachable from the container)
and tells it *where* to save each download. Movies land in the movies library
folder and TV shows in the tv folder, so Jellyfin picks them up automatically.
"""
import base64
import binascii
import logging
import re

import requests

from config import (
    QBIT_URL,
    QBIT_USERNAME,
    QBIT_PASSWORD,
    QBIT_MOVIES_PATH,
    QBIT_TV_PATH,
    QBIT_MOVIES_CATEGORY,
    QBIT_TV_CATEGORY,
)

log = logging.getLogger("torrentbot.qbit")

# A single authenticated session is reused across calls; qBittorrent hands back
# a cookie on login that we keep for subsequent requests.
_session: requests.Session | None = None


def _login() -> requests.Session:
    """Authenticate against qBittorrent and cache the session."""
    global _session
    s = requests.Session()
    resp = s.post(
        f"{QBIT_URL}/api/v2/auth/login",
        data={"username": QBIT_USERNAME, "password": QBIT_PASSWORD},
        headers={"Referer": QBIT_URL},
        timeout=15,
    )
    resp.raise_for_status()
    if resp.text.strip() != "Ok.":
        raise RuntimeError("qBittorrent login failed (check QBIT_USERNAME/QBIT_PASSWORD)")
    _session = s
    log.info("qBittorrent login ok")
    return s


def _client() -> requests.Session:
    return _session if _session is not None else _login()


def _target(kind: str) -> tuple[str, str]:
    """Return (save_path, category) for a movie or tv download."""
    if kind == "tv":
        return QBIT_TV_PATH, QBIT_TV_CATEGORY
    return QBIT_MOVIES_PATH, QBIT_MOVIES_CATEGORY


_BTIH_RE = re.compile(r"xt=urn:btih:([0-9a-zA-Z]+)", re.IGNORECASE)


def _magnet_hash(magnet: str) -> str | None:
    """Extract the v1 info-hash (hex) from a magnet link, if present.

    qBittorrent reports hashes as 40-char hex, but magnets may carry either a
    40-char hex or a 32-char base32 btih. Normalize base32 to hex so we can
    compare against what qBittorrent returns.
    """
    m = _BTIH_RE.search(magnet or "")
    if not m:
        return None
    h = m.group(1)
    if len(h) == 40:
        return h.lower()
    if len(h) == 32:
        try:
            return base64.b32decode(h.upper()).hex()
        except (binascii.Error, ValueError):
            return h.lower()
    return h.lower()


def _existing_hashes(s: requests.Session) -> set[str]:
    """Return the set of info-hashes currently known to qBittorrent (hex)."""
    resp = s.get(f"{QBIT_URL}/api/v2/torrents/info", timeout=15)
    if resp.status_code == 403:
        s = _login()
        resp = s.get(f"{QBIT_URL}/api/v2/torrents/info", timeout=15)
    resp.raise_for_status()
    return {str(t.get("hash", "")).lower() for t in resp.json()}


def magnet_hash(magnet: str) -> str | None:
    """Public helper: the v1 info-hash (hex) of a magnet, or None."""
    return _magnet_hash(magnet)


def _shape(t: dict) -> dict:
    """Normalize a raw qBittorrent torrent into a compact, human-friendly dict."""
    return {
        "name": t.get("name", "?"),
        "progress": round(float(t.get("progress", 0)) * 100, 1),
        "state": t.get("state", "?"),
        "category": t.get("category", ""),
    }


def add_magnet(magnet: str, kind: str = "movie") -> str:
    """Queue a magnet in qBittorrent, saving it into the right Jellyfin folder.

    `kind` is "movie" or "tv" and selects the destination folder/category.

    Returns:
        "added"     - the magnet was queued.
        "duplicate" - the torrent was already present in qBittorrent.

    Raises on unrecoverable errors so the caller can warn the user.
    """
    save_path, category = _target(kind)
    data = {
        "urls": magnet,
        "savepath": save_path,
        "category": category,
        # Turn off Automatic Torrent Management so the explicit savepath wins.
        "autoTMM": "false",
    }

    s = _client()
    resp = s.post(
        f"{QBIT_URL}/api/v2/torrents/add",
        data=data,
        headers={"Referer": QBIT_URL},
        timeout=30,
    )
    # The cached cookie may have expired; log in again once and retry.
    if resp.status_code == 403:
        s = _login()
        resp = s.post(
            f"{QBIT_URL}/api/v2/torrents/add",
            data=data,
            headers={"Referer": QBIT_URL},
            timeout=30,
        )
    resp.raise_for_status()

    if resp.text.strip().lower() == "fails.":
        # qBittorrent returns "Fails." when it couldn't add ANY of the torrents.
        # The most common reason is that this exact torrent is already in the
        # list, so check its info-hash before treating it as an error.
        want = _magnet_hash(magnet)
        try:
            already_present = bool(want) and want in _existing_hashes(s)
        except Exception:
            log.warning("could not verify existing torrents after add failure", exc_info=True)
            already_present = False

        if already_present:
            log.info("magnet already present in qBittorrent (hash=%s)", want)
            return "duplicate"

        log.error(
            "qBittorrent rejected the magnet (savepath=%s category=%s hash=%s response=%r)",
            save_path, category, want, resp.text.strip(),
        )
        raise RuntimeError("qBittorrent rejected the magnet")

    log.info("queued magnet into %s (category=%s)", save_path, category)
    return "added"


def list_transfers() -> list[dict]:
    """Return the current torrents with a compact, human-friendly shape."""
    s = _client()
    resp = s.get(f"{QBIT_URL}/api/v2/torrents/info", timeout=15)
    if resp.status_code == 403:
        s = _login()
        resp = s.get(f"{QBIT_URL}/api/v2/torrents/info", timeout=15)
    resp.raise_for_status()
    return [_shape(t) for t in resp.json()]


def get_transfer(info_hash: str) -> dict | None:
    """Return the compact transfer dict for a single info-hash, or None.

    Used to poll whether a specific download has finished so the bot can trigger
    a Jellyfin library rescan at the right time.
    """
    if not info_hash:
        return None
    s = _client()
    params = {"hashes": info_hash}
    resp = s.get(f"{QBIT_URL}/api/v2/torrents/info", params=params, timeout=15)
    if resp.status_code == 403:
        s = _login()
        resp = s.get(f"{QBIT_URL}/api/v2/torrents/info", params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return _shape(data[0]) if data else None
