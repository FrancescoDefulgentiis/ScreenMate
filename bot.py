import html
import logging
import asyncio
import contextlib
import time
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
)
from telegram.constants import ParseMode, ChatAction
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from config import TELEGRAM_TOKEN, ALLOWED_USERS
from llm import parse_intent, chat_reply, warmup
from jackett import search_torrents
from qbit import add_magnet, list_transfers, get_transfer, magnet_hash
from fladder import refresh_library
import prefs

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("torrentbot")

# ---------------------------------------------------------------------------
# Config / constants
# ---------------------------------------------------------------------------

# Languages the bot itself can speak (UI language). Key = code stored in prefs
# and passed to the LLM, value = label shown in the picker.
LANGUAGES = {
    "en": "English",
    "it": "Italiano",
    "es": "Espanol",
    "fr": "Francais",
    "de": "Deutsch",
}
DEFAULT_LANG = "en"

# Preferred film/content audio languages offered in /options. Keys match the
# short audio codes jackett.py emits (see LANG_TAGS there), so a stored value
# can be compared directly against a release's parsed audio track.
CONTENT_LANGUAGES = {
    "EN": "English",
    "IT": "Italiano",
    "ES": "Espanol",
    "FR": "Francais",
    "DE": "Deutsch",
}
DEFAULT_CONTENT_LANG = "EN"

# Shown at the very first interaction, before we know the user's language, so it
# greets in a few languages and lets the self-explanatory buttons do the rest.
ONBOARDING_GREETING = (
    "\U0001F44B Welcome to ScreenMate! / Benvenuto! / Bienvenido! / Bienvenue! / Willkommen!\n\n"
    "First, pick the language you want me to talk to you in:"
)

# How many results to show the user (filtered + sorted).
MAX_RESULTS = 5

# A queued magnet only *starts* downloading, so after adding it we watch the
# torrent in qBittorrent and trigger the Jellyfin rescan once it actually
# finishes (or, if we can't track it, after a plain delay as a fallback).
REFRESH_DELAY = 120          # grace period before the first completion check
REFRESH_POLL_INTERVAL = 30   # seconds between completion checks
REFRESH_MAX_WAIT = 12 * 3600 # stop watching a download after this long

# Small localized strings so the bot doesn't sound robotic in every language.
STRINGS = {
    "welcome": {
        "en": "Hey! I'm your movie & series buddy. Ask me what to watch, or just tell me what you feel like downloading.",
        "it": "Ehi! Sono il tuo compagno di film e serie. Chiedimi cosa guardare, o dimmi cosa vuoi scaricare.",
        "es": "Hola! Soy tu companero de pelis y series. Preguntame que ver, o dime que quieres descargar.",
        "fr": "Salut ! Je suis ton copain films & series. Demande-moi quoi regarder, ou dis-moi quoi telecharger.",
        "de": "Hi! Ich bin dein Film- & Serien-Buddy. Frag mich, was du schauen sollst, oder sag mir, was du laden willst.",
    },
    "not_authorized": {
        "en": "Sorry, you're not authorized to use this bot.",
        "it": "Spiacente, non sei autorizzato a usare questo bot.",
        "es": "Lo siento, no estas autorizado a usar este bot.",
        "fr": "Desole, tu n'es pas autorise a utiliser ce bot.",
        "de": "Sorry, du bist nicht berechtigt, diesen Bot zu nutzen.",
    },
    "pick_language": {
        "en": "Pick the language you want me to speak:",
        "it": "Scegli la lingua in cui vuoi che ti parli:",
        "es": "Elige el idioma en el que quieres que hable:",
        "fr": "Choisis la langue dans laquelle je dois parler :",
        "de": "Waehle die Sprache, in der ich sprechen soll:",
    },
    "language_set": {
        "en": "Done, I'll talk to you in English from now on.",
        "it": "Fatto, d'ora in poi ti parlo in italiano.",
        "es": "Listo, a partir de ahora te hablo en espanol.",
        "fr": "C'est fait, je te parle en francais desormais.",
        "de": "Erledigt, ab jetzt spreche ich Deutsch mit dir.",
    },
    "searching": {
        "en": "Looking for \"{q}\"... give me a sec.",
        "it": "Cerco \"{q}\"... un attimo.",
        "es": "Buscando \"{q}\"... dame un segundo.",
        "fr": "Je cherche \"{q}\"... un instant.",
        "de": "Ich suche \"{q}\"... einen Moment.",
    },
    "no_results": {
        "en": "Couldn't find anything solid for that. Want to try another title?",
        "it": "Non ho trovato niente di buono. Proviamo con un altro titolo?",
        "es": "No encontre nada solido. Probamos con otro titulo?",
        "fr": "Je n'ai rien trouve de correct. On essaie un autre titre ?",
        "de": "Nichts Brauchbares gefunden. Anderer Titel?",
    },
    "pick_one": {
        "en": "Here are the best options. Tap one to download:",
        "it": "Ecco le opzioni migliori. Toccane una per scaricare:",
        "es": "Estas son las mejores opciones. Toca una para descargar:",
        "fr": "Voici les meilleures options. Touche-en une pour telecharger :",
        "de": "Hier die besten Optionen. Tippe eine zum Herunterladen an:",
    },
    "queued": {
        "en": "On it! Sending to pepone and saving into the {folder} folder.\n<b>{title}</b>",
        "it": "Ci penso io! Mando a pepone e salvo nella cartella {folder}.\n<b>{title}</b>",
        "es": "Voy! Lo mando a pepone y lo guardo en la carpeta {folder}.\n<b>{title}</b>",
        "fr": "C'est parti ! J'envoie a pepone et je range dans le dossier {folder}.\n<b>{title}</b>",
        "de": "Mach ich! Schicke an pepone und speichere im Ordner {folder}.\n<b>{title}</b>",
    },
    "already_downloading": {
        "en": "That one's already in your downloads \U0001F44D\n<b>{title}</b>",
        "it": "Quello e gia nei tuoi download \U0001F44D\n<b>{title}</b>",
        "es": "Ese ya esta en tus descargas \U0001F44D\n<b>{title}</b>",
        "fr": "Celui-la est deja dans tes telechargements \U0001F44D\n<b>{title}</b>",
        "de": "Das ist schon in deinen Downloads \U0001F44D\n<b>{title}</b>",
    },
    "download_ready": {
        "en": "\U0001F3AC Done! <b>{title}</b> finished downloading and is ready in Jellyfin.",
        "it": "\U0001F3AC Fatto! <b>{title}</b> ha finito di scaricare ed e pronto su Jellyfin.",
        "es": "\U0001F3AC Listo! <b>{title}</b> termino de descargarse y ya esta en Jellyfin.",
        "fr": "\U0001F3AC Termine ! <b>{title}</b> a fini de telecharger et est pret sur Jellyfin.",
        "de": "\U0001F3AC Fertig! <b>{title}</b> ist fertig heruntergeladen und in Jellyfin verfuegbar.",
    },
    "expired": {
        "en": "That list expired. Just tell me again what you want.",
        "it": "Quella lista e scaduta. Dimmi di nuovo cosa vuoi.",
        "es": "Esa lista expiro. Dime otra vez que quieres.",
        "fr": "Cette liste a expire. Redis-moi ce que tu veux.",
        "de": "Die Liste ist abgelaufen. Sag mir nochmal, was du willst.",
    },
    "oops": {
        "en": "Something went wrong on my side. Try again in a moment.",
        "it": "Qualcosa e andato storto da parte mia. Riprova tra poco.",
        "es": "Algo salio mal por mi lado. Intenta de nuevo en un momento.",
        "fr": "Un souci de mon cote. Reessaie dans un instant.",
        "de": "Bei mir ist etwas schiefgelaufen. Versuch es gleich nochmal.",
    },
    "pick_content_language": {
        "en": "And which language do you prefer for the films themselves?",
        "it": "E in che lingua preferisci i film?",
        "es": "Y en que idioma prefieres las peliculas?",
        "fr": "Et dans quelle langue preferes-tu les films ?",
        "de": "Und in welcher Sprache bevorzugst du die Filme?",
    },
    "content_language_set": {
        "en": "Great, I'll favour {name} films from now on.",
        "it": "Ottimo, d'ora in poi preferiro i film in {name}.",
        "es": "Genial, a partir de ahora preferire las pelis en {name}.",
        "fr": "Parfait, je privilegierai les films en {name} desormais.",
        "de": "Super, ab jetzt bevorzuge ich Filme auf {name}.",
    },
    "options_menu": {
        "en": "Settings - what would you like to change?",
        "it": "Impostazioni - cosa vuoi modificare?",
        "es": "Ajustes - que quieres cambiar?",
        "fr": "Reglages - que veux-tu modifier ?",
        "de": "Einstellungen - was moechtest du aendern?",
    },
    "opt_bot_language": {
        "en": "\U0001F5E3 Bot language: {name}",
        "it": "\U0001F5E3 Lingua del bot: {name}",
        "es": "\U0001F5E3 Idioma del bot: {name}",
        "fr": "\U0001F5E3 Langue du bot : {name}",
        "de": "\U0001F5E3 Bot-Sprache: {name}",
    },
    "opt_film_language": {
        "en": "\U0001F3AC Film language: {name}",
        "it": "\U0001F3AC Lingua dei film: {name}",
        "es": "\U0001F3AC Idioma de las pelis: {name}",
        "fr": "\U0001F3AC Langue des films : {name}",
        "de": "\U0001F3AC Film-Sprache: {name}",
    },
    "no_downloads": {
        "en": "Nothing is downloading right now. Everything's done!",
        "it": "Non c'e nessun download in corso. E tutto pronto!",
        "es": "No hay descargas en curso. Esta todo listo!",
        "fr": "Aucun telechargement en cours. Tout est pret !",
        "de": "Gerade laeuft kein Download. Alles fertig!",
    },
    "downloads_header": {
        "en": "\U0001F4E5 {n} download(s) still in progress:",
        "it": "\U0001F4E5 {n} download ancora in corso:",
        "es": "\U0001F4E5 {n} descarga(s) aun en curso:",
        "fr": "\U0001F4E5 {n} telechargement(s) encore en cours :",
        "de": "\U0001F4E5 {n} laufende(r) Download(s):",
    },
}

# Friendly, localized labels for the qBittorrent download-phase states we may
# show in /downloads. Unknown states fall back to the raw qBittorrent value.
STATE_LABELS = {
    "downloading": {"en": "downloading", "it": "in download", "es": "descargando", "fr": "telechargement", "de": "laedt"},
    "forcedDL": {"en": "downloading", "it": "in download", "es": "descargando", "fr": "telechargement", "de": "laedt"},
    "metaDL": {"en": "fetching metadata", "it": "recupero info", "es": "obteniendo info", "fr": "recup. infos", "de": "hole Infos"},
    "stalledDL": {"en": "stalled", "it": "in stallo", "es": "estancado", "fr": "au point mort", "de": "steht still"},
    "queuedDL": {"en": "queued", "it": "in coda", "es": "en cola", "fr": "en file", "de": "in Warteschlange"},
    "pausedDL": {"en": "paused", "it": "in pausa", "es": "en pausa", "fr": "en pause", "de": "pausiert"},
    "checkingDL": {"en": "checking", "it": "verifica", "es": "verificando", "fr": "verification", "de": "pruefe"},
    "allocating": {"en": "allocating", "it": "allocazione", "es": "asignando", "fr": "allocation", "de": "reserviere"},
    "moving": {"en": "moving", "it": "spostamento", "es": "moviendo", "fr": "deplacement", "de": "verschiebe"},
    "error": {"en": "error", "it": "errore", "es": "error", "fr": "erreur", "de": "Fehler"},
    "missingFiles": {"en": "missing files", "it": "file mancanti", "es": "faltan archivos", "fr": "fichiers manquants", "de": "fehlende Dateien"},
}


def t(key: str, lang: str, **kw) -> str:
    """Fetch a localized string, falling back to English."""
    text = STRINGS.get(key, {}).get(lang) or STRINGS.get(key, {}).get(DEFAULT_LANG, "")
    return text.format(**kw) if kw else text


# ---------------------------------------------------------------------------
# Per-user session state (in-memory)
# ---------------------------------------------------------------------------
# SESSIONS[user_id] = {
#   "ui_lang": "en",         # language the bot talks in
#   "content_lang": "EN",    # preferred film/audio language
#   "onboarded": bool,       # both languages chosen at least once
#   "onboarding": bool,      # currently walking through first-start setup
#   "history": [{"role": "user"/"assistant", "content": "..."}],  # rolling context
#   "pending": {"type": "movie"/"tv", "results": [...]} or None,
# }
SESSIONS: dict[int, dict] = {}
HISTORY_LIMIT = 12  # keep the last N turns for context


def session(user_id: int) -> dict:
    s = SESSIONS.get(user_id)
    if s is None:
        stored = prefs.get(user_id)
        s = {
            "ui_lang": stored.get("ui_lang", DEFAULT_LANG),
            "content_lang": stored.get("content_lang", DEFAULT_CONTENT_LANG),
            # A user is onboarded only once both preferences have been stored.
            "onboarded": bool(stored.get("ui_lang") and stored.get("content_lang")),
            "onboarding": False,
            "history": [],
            "pending": None,
        }
        SESSIONS[user_id] = s
    return s


def needs_onboarding(s: dict) -> bool:
    return not s.get("onboarded")


def remember(s: dict, role: str, content: str):
    s["history"].append({"role": role, "content": content})
    if len(s["history"]) > HISTORY_LIMIT:
        s["history"] = s["history"][-HISTORY_LIMIT:]


def allowed(update: Update) -> bool:
    user = update.effective_user
    return user is not None and user.id in ALLOWED_USERS


# ---------------------------------------------------------------------------
# "Bot is thinking" typing indicator
# ---------------------------------------------------------------------------

# Telegram clears the "typing..." status after ~5s, so it must be re-sent while
# a slow call is running.
_TYPING_REFRESH = 4.0


@contextlib.asynccontextmanager
async def typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Show Telegram's "typing..." action until the wrapped block finishes.

    A background task re-sends the chat action every few seconds so the
    animation stays visible across long LLM/search calls. Pair this with
    ``run_blocking`` so the event loop stays free to keep refreshing it.
    """
    async def _keep_typing():
        try:
            while True:
                try:
                    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
                except Exception:
                    log.debug("send_chat_action failed", exc_info=True)
                await asyncio.sleep(_TYPING_REFRESH)
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(_keep_typing())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def run_blocking(func, *args):
    """Run a blocking (requests-based) call off the event loop.

    Keeps the loop responsive so the typing indicator keeps refreshing while
    the network call is in flight.
    """
    return await asyncio.to_thread(func, *args)


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------

def filter_and_rank(results: list[dict], content_lang: str | None = None) -> list[dict]:
    """Keep the healthiest torrents, biased toward the preferred film language.

    Drops 0-seeder junk, then sorts so releases whose audio matches the user's
    preferred content language (or are multi-audio) come first, and by seeders
    within each group. Finally caps the count.
    """
    cleaned = [r for r in results if int(r.get("seeders", 0)) > 0]
    if not cleaned:
        cleaned = results  # nothing had seeders; show what we have

    want = (content_lang or "").upper()

    def prefers_lang(r: dict) -> bool:
        if not want:
            return False
        audio = str(r.get("audio", "")).upper()
        tracks = audio.split("/")
        return want in tracks or "MULTI" in tracks

    cleaned.sort(
        key=lambda r: (prefers_lang(r), int(r.get("seeders", 0))),
        reverse=True,
    )
    return cleaned[:MAX_RESULTS]


def _short(title: str, width: int = 34) -> str:
    title = title.replace("\n", " ").strip()
    return title if len(title) <= width else title[: width - 1] + "\u2026"


def render_table(results: list[dict]) -> str:
    """Build a monospace table that aligns correctly in Telegram (HTML <pre>)."""
    header = f"{'#':<2} {'Title':<22} {'Seed':>5} {'Size':>6} {'Audio':<9} {'Subs':<6}"
    sep = "-" * len(header)
    rows = [header, sep]
    for i, r in enumerate(results, 1):
        rows.append(
            f"{i:<2} {_short(r['title'], 22):<22} "
            f"{int(r.get('seeders', 0)):>5} {float(r.get('size_gb', 0)):>5.1f}G "
            f"{str(r.get('audio', '?')):<9} {str(r.get('subs', '?')):<6}"
        )
    return "<pre>" + "\n".join(rows) + "</pre>"


def result_keyboard(results: list[dict]) -> InlineKeyboardMarkup:
    # One row of numbered buttons (1..N) so it stays compact.
    row = [InlineKeyboardButton(str(i + 1), callback_data=f"dl:{i}") for i in range(len(results))]
    return InlineKeyboardMarkup([row])


def ui_language_keyboard(s: dict) -> InlineKeyboardMarkup:
    """Picker for the language the bot speaks."""
    buttons = [
        [InlineKeyboardButton(
            ("\u2705 " if code == s.get("ui_lang") else "") + label,
            callback_data=f"setui:{code}",
        )]
        for code, label in LANGUAGES.items()
    ]
    return InlineKeyboardMarkup(buttons)


def content_language_keyboard(s: dict) -> InlineKeyboardMarkup:
    """Picker for the preferred film/audio language."""
    buttons = [
        [InlineKeyboardButton(
            ("\u2705 " if code == s.get("content_lang") else "") + label,
            callback_data=f"setcontent:{code}",
        )]
        for code, label in CONTENT_LANGUAGES.items()
    ]
    return InlineKeyboardMarkup(buttons)


def options_keyboard(s: dict) -> InlineKeyboardMarkup:
    """Top-level settings menu showing the two current selections."""
    ui_label = LANGUAGES.get(s.get("ui_lang"), s.get("ui_lang", "?"))
    content_label = CONTENT_LANGUAGES.get(s.get("content_lang"), s.get("content_lang", "?"))
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t("opt_bot_language", s["ui_lang"], name=ui_label), callback_data="opt:ui")],
        [InlineKeyboardButton(t("opt_film_language", s["ui_lang"], name=content_label), callback_data="opt:content")],
    ])


# ---------------------------------------------------------------------------
# Download status rendering
# ---------------------------------------------------------------------------

# qBittorrent states that mean the download itself is finished (seeding/done).
_DONE_STATES = {
    "uploading", "stalledUP", "pausedUP", "queuedUP",
    "completedUP", "forcedUP", "checkingUP",
}


def _is_complete(tr: dict) -> bool:
    """True when a transfer has finished downloading (fully or seeding)."""
    if float(tr.get("progress", 0)) >= 100:
        return True
    return tr.get("state") in _DONE_STATES


def _state_label(state: str, lang: str) -> str:
    entry = STATE_LABELS.get(state)
    if not entry:
        return state
    return entry.get(lang) or entry.get(DEFAULT_LANG, state)


def _progress_bar(pct: float, width: int = 10) -> str:
    pct = max(0.0, min(100.0, float(pct)))
    filled = int(round(pct / 100 * width))
    return "\u2588" * filled + "\u2591" * (width - filled)


def render_downloads(transfers: list[dict], lang: str) -> str:
    """Build an HTML summary of the in-progress downloads."""
    lines = [t("downloads_header", lang, n=len(transfers))]
    for tr in transfers:
        name = html.escape(_short(tr.get("name", "?"), 40))
        pct = float(tr.get("progress", 0))
        bar = _progress_bar(pct)
        state = html.escape(_state_label(tr.get("state", "?"), lang))
        lines.append(f"<b>{name}</b>\n<code>{bar}</code> {pct:.1f}% \u00b7 {state}")
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def begin_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE, s: dict):
    """Kick off first-start setup: ask for the bot language, then the film language."""
    s["onboarding"] = True
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=ONBOARDING_GREETING,
        reply_markup=ui_language_keyboard(s),
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        await update.message.reply_text(t("not_authorized", DEFAULT_LANG))
        return
    s = session(update.effective_user.id)
    if needs_onboarding(s):
        await begin_onboarding(update, context, s)
        return
    await update.message.reply_text(t("welcome", s["ui_lang"]))


async def options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        await update.message.reply_text(t("not_authorized", DEFAULT_LANG))
        return
    s = session(update.effective_user.id)
    if needs_onboarding(s):
        await begin_onboarding(update, context, s)
        return
    await update.message.reply_text(
        t("options_menu", s["ui_lang"]),
        reply_markup=options_keyboard(s),
    )


async def downloads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        await update.message.reply_text(t("not_authorized", DEFAULT_LANG))
        return
    s = session(update.effective_user.id)
    if needs_onboarding(s):
        await begin_onboarding(update, context, s)
        return
    lang = s["ui_lang"]
    try:
        async with typing(context, update.effective_chat.id):
            transfers = await run_blocking(list_transfers)
    except Exception:
        log.exception("list_transfers failed")
        await update.message.reply_text(t("oops", lang))
        return

    active = [tr for tr in transfers if not _is_complete(tr)]
    if not active:
        await update.message.reply_text(t("no_downloads", lang))
        return

    await update.message.reply_text(
        render_downloads(active, lang),
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Settings / language callbacks
# ---------------------------------------------------------------------------

async def on_options_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle taps on the top-level /options menu."""
    query = update.callback_query
    await query.answer()
    s = session(query.from_user.id)
    what = query.data.split(":", 1)[1]
    if what == "ui":
        await query.edit_message_text(
            t("pick_language", s["ui_lang"]),
            reply_markup=ui_language_keyboard(s),
        )
    elif what == "content":
        await query.edit_message_text(
            t("pick_content_language", s["ui_lang"]),
            reply_markup=content_language_keyboard(s),
        )


async def on_ui_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    s = session(query.from_user.id)
    code = query.data.split(":", 1)[1]
    if code not in LANGUAGES:
        return
    s["ui_lang"] = code
    prefs.update(query.from_user.id, ui_lang=code)

    if s.get("onboarding"):
        # Onboarding step 2: now ask for the preferred film language.
        await query.edit_message_text(t("language_set", s["ui_lang"]))
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=t("pick_content_language", s["ui_lang"]),
            reply_markup=content_language_keyboard(s),
        )
    else:
        await query.edit_message_text(t("language_set", s["ui_lang"]))


async def on_content_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    s = session(query.from_user.id)
    code = query.data.split(":", 1)[1]
    if code not in CONTENT_LANGUAGES:
        return
    s["content_lang"] = code
    prefs.update(query.from_user.id, content_lang=code)
    label = CONTENT_LANGUAGES[code]

    await query.edit_message_text(t("content_language_set", s["ui_lang"], name=label))
    if s.get("onboarding"):
        # Onboarding complete: both languages are now set.
        s["onboarding"] = False
        s["onboarded"] = True
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=t("welcome", s["ui_lang"]),
        )


# ---------------------------------------------------------------------------
# Main conversation handler
# ---------------------------------------------------------------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        await update.message.reply_text(t("not_authorized", DEFAULT_LANG))
        return

    s = session(update.effective_user.id)
    if needs_onboarding(s):
        await begin_onboarding(update, context, s)
        return
    lang = s["ui_lang"]
    text = update.message.text
    remember(s, "user", text)

    # Ask the LLM what the user actually wants.
    # Expected shape from llm.parse_intent(text, history):
    #   {"action": "chat"}                          -> just talk / recommend
    #   {"action": "download", "type": "movie"|"tv",
    #    "title": "...", "year": 2021,
    #    "season": 1, "episode": 3}                 -> user committed to downloading
    try:
        async with typing(context, update.effective_chat.id):
            intent = await run_blocking(parse_intent, text, s["history"])
    except Exception as e:
        log.exception("parse_intent failed")
        await update.message.reply_text(t("oops", lang))
        return

    if intent.get("action") != "download":
        # Pure conversation: let the LLM be the movie buddy.
        try:
            async with typing(context, update.effective_chat.id):
                reply = await run_blocking(chat_reply, s["history"], lang)
        except Exception:
            log.exception("chat_reply failed")
            await update.message.reply_text(t("oops", lang))
            return
        remember(s, "assistant", reply)
        await update.message.reply_text(reply)
        return

    # --- User explicitly wants to download something ---
    await _do_search(update, context, s, intent)


async def _do_search(update: Update, context: ContextTypes.DEFAULT_TYPE, s: dict, intent: dict):
    lang = s["ui_lang"]

    query = intent["title"]
    if intent.get("year"):
        query += f" {intent['year']}"
    if intent.get("type") == "tv" and intent.get("season"):
        query += f" S{int(intent['season']):02d}"
        if intent.get("episode"):
            query += f"E{int(intent['episode']):02d}"

    await update.message.reply_text(t("searching", lang, q=query))

    try:
        async with typing(context, update.effective_chat.id):
            results = await run_blocking(search_torrents, query)
    except Exception:
        log.exception("search_torrents failed")
        await update.message.reply_text(t("oops", lang))
        return

    results = filter_and_rank(results or [], s.get("content_lang"))
    if len(results) < 1:
        await update.message.reply_text(t("no_results", lang))
        return

    s["pending"] = {"type": intent.get("type", "movie"), "results": results}

    await update.message.reply_text(render_table(results), parse_mode=ParseMode.HTML)
    await update.message.reply_text(
        t("pick_one", lang),
        reply_markup=result_keyboard(results),
    )


async def handle_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    s = session(query.from_user.id)
    lang = s["ui_lang"]

    pending = s.get("pending")
    if not pending:
        await query.edit_message_text(t("expired", lang))
        return

    try:
        idx = int(query.data.split(":", 1)[1])
        torrent = pending["results"][idx]
    except (ValueError, IndexError):
        await query.edit_message_text(t("expired", lang))
        return

    try:
        async with typing(context, query.message.chat_id):
            status = await run_blocking(add_magnet, torrent["magnet"], pending["type"])
    except Exception:
        log.exception("add_magnet failed")
        await query.edit_message_text(t("oops", lang))
        return

    folder = "movies" if pending["type"] == "movie" else "tv"
    s["pending"] = None
    key = "already_downloading" if status == "duplicate" else "queued"
    await query.edit_message_text(
        t(key, lang, folder=folder, title=torrent["title"]),
        parse_mode=ParseMode.HTML,
    )

    # Watch this download in the background and refresh Jellyfin once it lands.
    asyncio.create_task(
        _watch_and_refresh(
            context,
            query.message.chat_id,
            lang,
            magnet_hash(torrent["magnet"]),
            torrent["title"],
        )
    )


async def _watch_and_refresh(context: ContextTypes.DEFAULT_TYPE, chat_id: int,
                             lang: str, info_hash: str | None, title: str):
    """Wait for a queued download to finish, then trigger a Jellyfin rescan.

    Polls qBittorrent for the torrent's completion by info-hash and calls
    ``refresh_library`` once it's done, notifying the user. If the hash can't be
    determined we can't track completion, so we fall back to a single delayed
    refresh (a magnet needs time to land on disk before Jellyfin can see it).
    """
    # Give the magnet a moment to register in qBittorrent / start writing files.
    await asyncio.sleep(REFRESH_DELAY)

    if not info_hash:
        log.info("no info-hash for %r; doing a single delayed Jellyfin refresh", title)
        await run_blocking(refresh_library)
        return

    deadline = time.monotonic() + REFRESH_MAX_WAIT
    while time.monotonic() < deadline:
        try:
            tr = await run_blocking(get_transfer, info_hash)
        except Exception:
            log.debug("completion poll failed for %s", info_hash, exc_info=True)
            tr = None

        if tr and _is_complete(tr):
            log.info("download complete (hash=%s); refreshing Jellyfin", info_hash)
            ok = await run_blocking(refresh_library)
            if ok:
                with contextlib.suppress(Exception):
                    await context.bot.send_message(
                        chat_id,
                        t("download_ready", lang, title=html.escape(title)),
                        parse_mode=ParseMode.HTML,
                    )
            return

        await asyncio.sleep(REFRESH_POLL_INTERVAL)

    log.info("stopped watching download %s after max wait", info_hash)


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------
def main():
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(_post_init)
        # Be tolerant of a slow/flaky network to api.telegram.org: the default
        # 5s connect timeout is what was surfacing as telegram.error.TimedOut
        # and making commands look "ignored" when a reply failed to send.
        .connect_timeout(20.0)
        .read_timeout(20.0)
        .write_timeout(20.0)
        .pool_timeout(20.0)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    # Accept both /option and /options so either spelling opens the settings menu.
    app.add_handler(CommandHandler(["option", "options"], options))
    app.add_handler(CommandHandler("downloads", downloads))

    # Callback routing: settings menu, language pickers, download picker.
    app.add_handler(CallbackQueryHandler(on_options_nav, pattern=r"^opt:"))
    app.add_handler(CallbackQueryHandler(on_ui_language, pattern=r"^setui:"))
    app.add_handler(CallbackQueryHandler(on_content_language, pattern=r"^setcontent:"))
    app.add_handler(CallbackQueryHandler(handle_choice, pattern=r"^dl:"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.add_error_handler(on_error)

    log.info("torrentbot started")
    app.run_polling()


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log any unhandled exception so a transient failure doesn't crash a handler.

    Telegram send/connect timeouts (telegram.error.TimedOut) are common on a
    slow network and are usually harmless — the user can just retry — so we log
    them and move on instead of letting the traceback bubble up unhandled.
    """
    err = context.error
    if isinstance(err, TelegramError):
        log.warning("Telegram API error while handling an update: %s", err)
    else:
        log.exception("Unhandled exception while processing update", exc_info=err)


async def _post_init(app: Application):
    """Register the command list so it shows up in Telegram's menu."""
    await app.bot.set_my_commands([
        BotCommand("start", "Start the bot / set up your languages"),
        BotCommand("downloads", "Show downloads still in progress"),
        BotCommand("option", "Change your bot and film languages"),
    ])
    # Preload the LLM in the background so the first user message doesn't eat the
    # (slow, CPU-bound) model load. Best effort — never block startup on it.
    asyncio.create_task(asyncio.to_thread(warmup))


if __name__ == "__main__":
    main()
