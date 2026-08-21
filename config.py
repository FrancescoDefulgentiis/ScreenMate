"""Central configuration for ScreenMate.

Every value is read from an environment variable so that no secret ever has to
live in the source tree. When running under Docker these are supplied through
the `.env` file referenced by docker-compose; when running locally you can
export them in your shell (see `.env.example`).
"""
import os


def _int_set(raw: str) -> set[int]:
    """Parse a comma/space separated list of user ids into a set of ints."""
    out: set[int] = set()
    for chunk in raw.replace(",", " ").split():
        try:
            out.add(int(chunk))
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ALLOWED_USERS = _int_set(os.environ.get("ALLOWED_USERS", ""))

# ---------------------------------------------------------------------------
# Ollama (LLM)
# ---------------------------------------------------------------------------
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
# Default to a small 3B model so it stays responsive on CPU-only machines. Any
# Ollama model works; bump to a bigger one (e.g. qwen2.5:7b) if you have a GPU.
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b-instruct-q4_K_M")

# How long (seconds) to wait for a full generation to come back. CPU inference
# can be slow, so this is generous; the connect timeout below stays short so we
# fail fast when Ollama itself is unreachable.
OLLAMA_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "300"))
OLLAMA_CONNECT_TIMEOUT = float(os.environ.get("OLLAMA_CONNECT_TIMEOUT", "10"))

# Keep the model resident in RAM between requests so we pay the (slow) load cost
# only once instead of on every message. Accepts Ollama duration strings
# ("30m", "24h") or "-1" to keep it loaded indefinitely.
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "30m")

# Cap generated tokens so a runaway reply can't stall the bot. Intent replies are
# tiny JSON; chat replies are meant to be short and texty anyway.
OLLAMA_NUM_PREDICT_INTENT = int(os.environ.get("OLLAMA_NUM_PREDICT_INTENT", "100"))
OLLAMA_NUM_PREDICT_CHAT = int(os.environ.get("OLLAMA_NUM_PREDICT_CHAT", "400"))

# Retry transient connection failures (Ollama briefly unavailable). Read timeouts
# are NOT retried — retrying a genuinely slow generation just doubles the wait.
OLLAMA_RETRIES = int(os.environ.get("OLLAMA_RETRIES", "2"))

# ---------------------------------------------------------------------------
# Jackett (torrent search)
# ---------------------------------------------------------------------------
JACKETT_URL = os.environ.get("JACKETT_URL", "http://localhost:9117").rstrip("/")
JACKETT_API_KEY = os.environ.get("JACKETT_API_KEY", "")

# A search against "all" indexers waits for the slowest one, so give it a
# generous read window. The connect timeout stays short so we fail fast when
# Jackett itself is unreachable.
JACKETT_TIMEOUT = float(os.environ.get("JACKETT_TIMEOUT", "90"))
JACKETT_CONNECT_TIMEOUT = float(os.environ.get("JACKETT_CONNECT_TIMEOUT", "10"))

# ---------------------------------------------------------------------------
# Jellyfin (library refresh via fladder.py)
# ---------------------------------------------------------------------------
JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "http://localhost:8096").rstrip("/")
JELLYFIN_API_KEY = os.environ.get("JELLYFIN_API_KEY", "")

# ---------------------------------------------------------------------------
# qBittorrent (download client)
# ---------------------------------------------------------------------------
# URL of the qBittorrent Web UI / API. This can point at a qBittorrent that
# runs anywhere reachable from the container (another host, another container,
# etc.).
QBIT_URL = os.environ.get("QBIT_URL", "http://localhost:8081").rstrip("/")
QBIT_USERNAME = os.environ.get("QBIT_USERNAME", "")
QBIT_PASSWORD = os.environ.get("QBIT_PASSWORD", "")

# Where qBittorrent should drop finished files. These paths are interpreted by
# qBittorrent (i.e. they must exist on the machine running qBittorrent), and
# should point at the Jellyfin "movies" and "tv" libraries. When qBittorrent and
# this bot share the same host filesystem they line up with the volumes mounted
# in docker-compose.yml (/media/movies, /media/tv).
QBIT_MOVIES_PATH = os.environ.get("QBIT_MOVIES_PATH", "/home/pepone/plex/movies")
QBIT_TV_PATH = os.environ.get("QBIT_TV_PATH", "/home/pepone/plex/tv")

# Categories tag the torrents inside qBittorrent so they are easy to find.
QBIT_MOVIES_CATEGORY = os.environ.get("QBIT_MOVIES_CATEGORY", "movies")
QBIT_TV_CATEGORY = os.environ.get("QBIT_TV_CATEGORY", "tv")
