import re
import requests
import xml.etree.ElementTree as ET
from config import (
    JACKETT_URL,
    JACKETT_API_KEY,
    JACKETT_TIMEOUT,
    JACKETT_CONNECT_TIMEOUT,
)

# ---------------------------------------------------------------------------
# Language / subtitle parsing
# ---------------------------------------------------------------------------

# Map common release tags -> short codes we show to the user.
LANG_TAGS = {
    "ita": "IT", "italian": "IT",
    "eng": "EN", "english": "EN",
    "spa": "ES", "esp": "ES", "spanish": "ES", "castellano": "ES",
    "fre": "FR", "fra": "FR", "french": "FR", "vff": "FR", "truefrench": "FR",
    "ger": "DE", "deu": "DE", "german": "DE",
    "jpn": "JP", "japanese": "JP",
    "kor": "KO", "korean": "KO",
    "rus": "RU", "russian": "RU",
    "por": "PT", "portuguese": "PT",
    "nl": "NL", "dutch": "NL",
}

# Tags that mean "many languages" rather than one specific one.
MULTI_AUDIO = ("multi", "multilang", "multi-audio", "dual", "dual audio")

# Subtitle hints -> what we display.
SUB_HINTS = {
    "multisub": "multi", "multi-sub": "multi", "multisubs": "multi",
    "subs": "yes", "subbed": "yes", "sub": "yes",
    "vostfr": "FR", "vostit": "IT", "vose": "ES", "vost": "yes",
    "hardsub": "hardcoded", "hardcoded": "hardcoded",
}


def _tokenize(title: str) -> list[str]:
    """Split a release title on common separators."""
    return [t.lower() for t in re.split(r"[.\s\-_\[\]()]+", title) if t]


def parse_langs(title: str) -> tuple[str, str]:
    """Best-effort audio + subtitle extraction from a release title.

    Returns (audio, subs) as short human strings, e.g. ("IT/EN", "multi").
    Unknown -> "?" so we never lie about what's inside.
    """
    tokens = _tokenize(title)
    tset = set(tokens)
    low = title.lower()

    # audio languages
    audio: list[str] = []
    for tok in tokens:
        code = LANG_TAGS.get(tok)
        if code and code not in audio:
            audio.append(code)
    if any(m in low for m in MULTI_AUDIO):
        audio.append("multi")

    # subtitles
    subs = None
    for tag, val in SUB_HINTS.items():
        if tag in tset or tag in low:
            subs = val
            break

    audio_str = "/".join(dict.fromkeys(audio)) if audio else "?"
    subs_str = subs if subs else "?"
    return audio_str, subs_str


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_torrents(query: str, limit: int = 8) -> list[dict]:
    url = f"{JACKETT_URL}/api/v2.0/indexers/all/results/torznab/api"
    params = {"apikey": JACKETT_API_KEY, "t": "search", "q": query}
    # (connect, read): fail fast if Jackett is down, but allow a long read window
    # since aggregating all indexers can be slow.
    r = requests.get(
        url, params=params, timeout=(JACKETT_CONNECT_TIMEOUT, JACKETT_TIMEOUT)
    )
    r.raise_for_status()

    ns = {"torznab": "http://torznab.com/schemas/2015/feed"}
    root = ET.fromstring(r.content)
    items: list[dict] = []

    for item in root.findall(".//item"):
        title = item.findtext("title") or ""
        magnet = None
        size = int(item.findtext("size") or 0)
        seeders = 0

        # Some indexers expose an explicit "languages" attr -> prefer it.
        attr_langs: list[str] = []
        for attr in item.findall("torznab:attr", ns):
            name = attr.get("name")
            if name == "seeders":
                seeders = int(attr.get("value", 0))
            elif name == "magneturl":
                magnet = attr.get("value")
            elif name in ("languages", "language"):
                val = attr.get("value")
                if val:
                    code = LANG_TAGS.get(val.lower(), val.upper()[:2])
                    if code not in attr_langs:
                        attr_langs.append(code)

        if not magnet:
            link = item.findtext("link")
            if link and link.startswith("magnet:"):
                magnet = link

        if magnet:
            audio, subs = parse_langs(title)
            # Trust indexer-provided languages over the title guess.
            if attr_langs:
                audio = "/".join(attr_langs)
            items.append({
                "title": title,
                "seeders": seeders,
                "size_gb": round(size / (1024 ** 3), 2),
                "audio": audio,
                "subs": subs,
                "magnet": magnet,
            })

    items.sort(key=lambda x: x["seeders"], reverse=True)
    return items[:limit]
