import json
import logging
import time
import requests
from config import (
    OLLAMA_URL,
    OLLAMA_MODEL,
    OLLAMA_TIMEOUT,
    OLLAMA_CONNECT_TIMEOUT,
    OLLAMA_KEEP_ALIVE,
    OLLAMA_NUM_PREDICT_INTENT,
    OLLAMA_NUM_PREDICT_CHAT,
    OLLAMA_RETRIES,
)

log = logging.getLogger("torrentbot.llm")

LANG_NAMES = {
    "en": "English",
    "it": "Italian",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
}

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

INTENT_SYSTEM_PROMPT = """You are the intent classifier for a movie/TV chat bot.
Look at the conversation and the LAST user message, then decide what the user wants.

Reply ONLY with compact JSON. No prose, no markdown fences.

There are two possible actions:

1. The user is just chatting, asking for recommendations, discussing films/series,
   asking what to watch, or is still undecided. In that case reply:
   {"action": "chat"}

2. The user has CLEARLY committed to downloading a specific title
   (e.g. "download Dune", "get me Breaking Bad season 2", "yes grab it",
   "scaricami Inception"). Only then reply:
   {"action": "download", "type": "movie" | "tv", "title": "<clean title>",
    "year": <int or null>, "season": <int or null>, "episode": <int or null>}

Rules:
- Default to {"action": "chat"} whenever there is ANY doubt.
- Merely mentioning or asking about a movie is NOT a download request.
- If the user says "yes"/"that one"/"download it" after you recommended a title,
  use the conversation to fill in the title.
- For a tv show with no season/episode mentioned, set them null.
- If ambiguous whether movie or tv, guess from wording (default "movie")."""


def _chat_system_prompt(lang: str) -> str:
    lang_name = LANG_NAMES.get(lang, "English")
    return (
        f"You are a warm, friendly movie and TV series buddy. "
        f"You chat casually, recommend films and shows, help the user decide "
        f"what to watch, discuss plots, actors, and moods. Keep replies short, "
        f"natural and human — like texting a friend, not a manual. "
        f"Do NOT mention torrents, downloads, or technical steps unless the user "
        f"brings them up. When you recommend titles, name a few concrete ones. "
        f"ALWAYS write your reply in {lang_name}."
    )


# ---------------------------------------------------------------------------
# Ollama helper (/api/chat)
# ---------------------------------------------------------------------------

def _chat(messages: list[dict], temperature: float = 0.3,
          force_json: bool = False, num_predict: int | None = None) -> str:
    """Call Ollama's /api/chat endpoint with proper role messages.

    Keeps the model resident (``keep_alive``) so we don't pay the load cost on
    every message, caps the output length (``num_predict``) so a runaway reply
    can't stall the bot, and retries only transient *connection* failures — a
    slow generation (read timeout) is not retried, since that would just double
    the wait.
    """
    options: dict = {"temperature": temperature}
    if num_predict is not None:
        options["num_predict"] = num_predict

    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": options,
    }
    if force_json:
        payload["format"] = "json"

    # (connect, read): fail fast if Ollama is unreachable, but allow a long read
    # window for slow CPU generation.
    timeout = (OLLAMA_CONNECT_TIMEOUT, OLLAMA_TIMEOUT)

    last_err: Exception | None = None
    attempts = max(1, OLLAMA_RETRIES)
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.post(
                f"{OLLAMA_URL}/api/chat", json=payload, timeout=timeout
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"].strip()
        except (requests.ConnectionError, requests.exceptions.ChunkedEncodingError) as e:
            last_err = e
            if attempt < attempts:
                backoff = 1.5 * attempt
                log.warning(
                    "Ollama connection failed (attempt %d/%d), retrying in %.1fs: %s",
                    attempt, attempts, backoff, e,
                )
                time.sleep(backoff)
    raise last_err  # type: ignore[misc]


def warmup() -> bool:
    """Preload the model into RAM so the first real message isn't slow.

    Fired once at startup (best effort). Generating a single token is enough to
    force Ollama to load the weights and keep them warm for ``OLLAMA_KEEP_ALIVE``.
    """
    try:
        _chat(
            [{"role": "user", "content": "hi"}],
            temperature=0.0,
            num_predict=1,
        )
        log.info("Ollama model '%s' warmed up", OLLAMA_MODEL)
        return True
    except Exception:
        log.warning("Ollama warmup failed (model will load on first message)", exc_info=True)
        return False


def _extract_json(raw: str) -> dict:
    """Best-effort JSON extraction from a model reply."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if "\n" in raw:
            raw = raw.split("\n", 1)[-1]
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start : end + 1]
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Public API used by bot.py
# ---------------------------------------------------------------------------

def parse_intent(user_text: str, history: list[dict] | None = None) -> dict:
    """Classify the latest message as chat or a concrete download request.

    Returns:
        {"action": "chat"}
        or
        {"action": "download", "type": "movie"|"tv", "title": str,
         "year": int|None, "season": int|None, "episode": int|None}
    """
    history = history or []
    messages = [{"role": "system", "content": INTENT_SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({
        "role": "user",
        "content": f"{user_text}\n\nRespond with the intent JSON.",
    })

    try:
        raw = _chat(messages, temperature=0.1, force_json=True,
                    num_predict=OLLAMA_NUM_PREDICT_INTENT)
        data = _extract_json(raw)
    except Exception:
        log.exception("parse_intent: falling back to chat")
        return {"action": "chat"}

    if data.get("action") == "download" and data.get("title"):
        return {
            "action": "download",
            "type": data.get("type", "movie"),
            "title": data["title"],
            "year": data.get("year"),
            "season": data.get("season"),
            "episode": data.get("episode"),
        }
    return {"action": "chat"}


def chat_reply(history: list[dict], lang: str = "en") -> str:
    """Generate the movie-buddy conversational reply in the chosen language."""
    messages = [{"role": "system", "content": _chat_system_prompt(lang)}]
    messages.extend(history)
    return _chat(messages, temperature=0.7, num_predict=OLLAMA_NUM_PREDICT_CHAT)
