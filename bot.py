import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from config import TELEGRAM_TOKEN, ALLOWED_USERS
from llm import parse_intent, chat_reply
from jackett import search_torrents
from qbit import add_magnet, list_transfers
from fladder import refresh_library

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("torrentbot")

# ---------------------------------------------------------------------------
# Config / constants
# ---------------------------------------------------------------------------

# Languages offered in /options. Key = code passed to the LLM, value = label.
LANGUAGES = {
    "en": "English",
    "it": "Italiano",
    "es": "Espanol",
    "fr": "Francais",
    "de": "Deutsch",
}
DEFAULT_LANG = "en"

# How many results to show the user (filtered + sorted).
MIN_RESULTS = 3
MAX_RESULTS = 5

# Seconds to wait after queuing a magnet before asking Fladder/Jellyfin to
# rescan. A magnet only *starts* downloading, so give it time to land on disk.
REFRESH_DELAY = 120

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
}


def t(key: str, lang: str, **kw) -> str:
    """Fetch a localized string, falling back to English."""
    text = STRINGS.get(key, {}).get(lang) or STRINGS.get(key, {}).get(DEFAULT_LANG, "")
    return text.format(**kw) if kw else text


# ---------------------------------------------------------------------------
# Per-user session state (in-memory)
# ---------------------------------------------------------------------------
# SESSIONS[user_id] = {
#   "lang": "en",
#   "history": [{"role": "user"/"assistant", "content": "..."}],  # short rolling context
#   "pending": {"type": "movie"/"tv", "results": [...]} or None,
# }
SESSIONS: dict[int, dict] = {}
HISTORY_LIMIT = 12  # keep the last N turns for context


def session(user_id: int) -> dict:
    s = SESSIONS.get(user_id)
    if s is None:
        s = {"lang": DEFAULT_LANG, "history": [], "pending": None}
        SESSIONS[user_id] = s
    return s


def remember(s: dict, role: str, content: str):
    s["history"].append({"role": role, "content": content})
    if len(s["history"]) > HISTORY_LIMIT:
        s["history"] = s["history"][-HISTORY_LIMIT:]


def allowed(update: Update) -> bool:
    user = update.effective_user
    return user is not None and user.id in ALLOWED_USERS


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------

def filter_and_rank(results: list[dict]) -> list[dict]:
    """Keep the healthiest torrents: drop 0-seeder junk, sort by seeders, cap the count."""
    cleaned = [r for r in results if int(r.get("seeders", 0)) > 0]
    if not cleaned:
        cleaned = results  # nothing had seeders; show what we have
    cleaned.sort(key=lambda r: int(r.get("seeders", 0)), reverse=True)
    return cleaned[:MAX_RESULTS]


def _short(title: str, width: int = 34) -> str:
    title = title.replace("\n", " ").strip()
    return title if len(title) <= width else title[: width - 1] + "\u2026"


def render_table(results: list[dict]) -> str:
    """Build a monospace table that aligns correctly in Telegram (HTML <pre>)."""
    header = f"{'#':<2} {'Title':<28} {'Seed':>5} {'Size':>6} {'Audio':<9} {'Subs':<6}"
    sep = "-" * len(header)
    rows = [header, sep]
    for i, r in enumerate(results, 1):
        rows.append(
            f"{i:<2} {_short(r['title'], 28):<28} "
            f"{int(r.get('seeders', 0)):>5} {float(r.get('size_gb', 0)):>5.1f}G "
            f"{str(r.get('audio', '?')):<9} {str(r.get('subs', '?')):<6}"
        )
    return "<pre>" + "\n".join(rows) + "</pre>"


def result_keyboard(results: list[dict]) -> InlineKeyboardMarkup:
    # One row of numbered buttons (1..N) so it stays compact.
    row = [InlineKeyboardButton(str(i + 1), callback_data=f"dl:{i}") for i in range(len(results))]
    return InlineKeyboardMarkup([row])


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        await update.message.reply_text(t("not_authorized", DEFAULT_LANG))
        return
    s = session(update.effective_user.id)
    await update.message.reply_text(t("welcome", s["lang"]))


async def options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        await update.message.reply_text(t("not_authorized", DEFAULT_LANG))
        return
    s = session(update.effective_user.id)
    buttons = [
        [InlineKeyboardButton(
            ("\u2705 " if code == s["lang"] else "") + label,
            callback_data=f"lang:{code}",
        )]
        for code, label in LANGUAGES.items()
    ]
    await update.message.reply_text(
        t("pick_language", s["lang"]),
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def on_language_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    s = session(query.from_user.id)
    code = query.data.split(":", 1)[1]
    if code in LANGUAGES:
        s["lang"] = code
    await query.edit_message_text(t("language_set", s["lang"]))


# ---------------------------------------------------------------------------
# Main conversation handler
# ---------------------------------------------------------------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        await update.message.reply_text(t("not_authorized", DEFAULT_LANG))
        return

    s = session(update.effective_user.id)
    lang = s["lang"]
    text = update.message.text
    remember(s, "user", text)

    # Ask the LLM what the user actually wants.
    # Expected shape from llm.parse_intent(text, history):
    #   {"action": "chat"}                          -> just talk / recommend
    #   {"action": "download", "type": "movie"|"tv",
    #    "title": "...", "year": 2021,
    #    "season": 1, "episode": 3}                 -> user committed to downloading
    try:
        intent = parse_intent(text, s["history"])
    except Exception as e:
        log.exception("parse_intent failed")
        await update.message.reply_text(t("oops", lang))
        return

    if intent.get("action") != "download":
        # Pure conversation: let the LLM be the movie buddy.
        try:
            reply = chat_reply(s["history"], lang)
        except Exception:
            log.exception("chat_reply failed")
            await update.message.reply_text(t("oops", lang))
            return
        remember(s, "assistant", reply)
        await update.message.reply_text(reply)
        return

    # --- User explicitly wants to download something ---
    await _do_search(update, s, intent)


async def _do_search(update: Update, s: dict, intent: dict):
    lang = s["lang"]

    query = intent["title"]
    if intent.get("year"):
        query += f" {intent['year']}"
    if intent.get("type") == "tv" and intent.get("season"):
        query += f" S{int(intent['season']):02d}"
        if intent.get("episode"):
            query += f"E{int(intent['episode']):02d}"

    await update.message.reply_text(t("searching", lang, q=query))

    try:
        results = search_torrents(query)
    except Exception:
        log.exception("search_torrents failed")
        await update.message.reply_text(t("oops", lang))
        return

    results = filter_and_rank(results or [])
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
    lang = s["lang"]

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
        add_magnet(torrent["magnet"], pending["type"])
    except Exception:
        log.exception("add_magnet failed")
        await query.edit_message_text(t("oops", lang))
        return

    folder = "movies" if pending["type"] == "movie" else "tv"
    s["pending"] = None
    await query.edit_message_text(
        t("queued", lang, folder=folder, title=torrent["title"]),
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("options", options))

    # Callback routing: language picker vs download picker.
    app.add_handler(CallbackQueryHandler(on_language_choice, pattern=r"^lang:"))
    app.add_handler(CallbackQueryHandler(handle_choice, pattern=r"^dl:"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("torrentbot started")
    app.run_polling()


if __name__ == "__main__":
    main()
