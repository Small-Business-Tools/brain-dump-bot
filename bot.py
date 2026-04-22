import sys
sys.stdout.reconfigure(line_buffering=True)

import logging
import os
import asyncio
from aiohttp import web
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    MessageHandler, filters, ContextTypes
)

from claude_client import process_idea
from store import init_db
from digest import send_digest
from transcriber import transcribe_voice

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", 0))
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
PORT = int(os.environ.get("PORT", 8080))


def is_authorised(update: Update) -> bool:
    if ALLOWED_USER_ID == 0:
        return True
    return update.effective_user.id == ALLOWED_USER_ID


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    await update.message.reply_text(
        "Idea bot is running.\n\n"
        "Send me any idea — text or voice — and I'll capture, tag, and score it.\n\n"
        "Commands:\n"
        "/digest — get your top ideas right now\n"
        "/list — show all idea clusters\n"
        "/backup — back up your database to GitHub now\n"
        "/help — show this message"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    await start(update, context)


async def list_ideas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return

    from store import get_all_clusters
    clusters = get_all_clusters()

    if not clusters:
        await update.message.reply_text("No ideas stored yet. Send me something!")
        return

    lines = ["*Your idea clusters:*\n"]
    for c in clusters:
        lines.append(f"• *{c['name']}* — {c['summary']}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def digest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    await update.message.reply_text("Building your digest...")
    try:
        await send_digest(context.bot, update.effective_user.id)
    except Exception as e:
        logger.error(f"Digest error: {e}")
        await update.message.reply_text("Something went wrong building the digest.")


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    from backup import backup_database
    await update.message.reply_text("Starting backup...")
    result = await backup_database()
    await update.message.reply_text(result)


# ---------------------------------------------------------------------------
# Message handlers
# ---------------------------------------------------------------------------

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle plain text idea messages."""
    if not is_authorised(update):
        return

    text = update.message.text.strip()
    if not text:
        return

    await update.message.reply_text("Got it — processing...")

    try:
        reply = await process_idea(text)
        await update.message.reply_text(reply, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error processing text idea: {e}")
        await update.message.reply_text(
            "Something went wrong. Your idea has been saved — send /list to check."
        )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice note messages via Whisper transcription."""
    if not is_authorised(update):
        return

    await update.message.reply_text("🎙 Voice note received — transcribing...")

    try:
        # Download the voice file from Telegram
        voice = update.message.voice
        tg_file = await context.bot.get_file(voice.file_id)
        file_bytes = await tg_file.download_as_bytearray()

        # Transcribe via Whisper
        transcript = await transcribe_voice(bytes(file_bytes))

        if not transcript:
            # Transcription failed — save a placeholder to fallback cluster
            from store import save_entry, link_entry_to_cluster
            from claude_client import get_or_create_fallback_cluster
            entry_id = save_entry("[Voice note — transcription failed]")
            cluster_id = await get_or_create_fallback_cluster()
            link_entry_to_cluster(cluster_id, entry_id)
            await update.message.reply_text(
                "⚠️ I couldn't transcribe that voice note. "
                "It's been saved to your *Unprocessed ideas* cluster. "
                "Try sending it as text instead.",
                parse_mode="Markdown"
            )
            return

        # Show the transcript so you can confirm it was understood correctly
        await update.message.reply_text(f'📝 Heard: _"{transcript}"_', parse_mode="Markdown")

        # Run through the same pipeline as a text idea
        reply = await process_idea(transcript)
        await update.message.reply_text(reply, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error handling voice note: {e}")
        await update.message.reply_text(
            "Something went wrong with that voice note. Try sending your idea as text."
        )


# ---------------------------------------------------------------------------
# Webhook endpoint (for Siri Shortcut)
# ---------------------------------------------------------------------------

async def handle_webhook(request: web.Request) -> web.Response:
    """Accept ideas from the Siri Shortcut via HTTP POST."""
    secret = request.headers.get("X-Secret", "")
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        return web.Response(status=401, text="Unauthorised")

    try:
        data = await request.json()
        idea_text = data.get("idea", "").strip()
    except Exception:
        return web.Response(status=400, text="Invalid JSON")

    if not idea_text:
        return web.Response(status=400, text="No idea text provided")

    try:
        reply = await process_idea(idea_text)
        return web.Response(status=200, text=reply)
    except Exception as e:
        logger.error(f"Webhook processing error: {e}")
        return web.Response(status=500, text="Processing failed — idea saved to fallback")


# ---------------------------------------------------------------------------
# App startup
# ---------------------------------------------------------------------------

def main():
    init_db()

    app = ApplicationBuilder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("list", list_ideas))
    app.add_handler(CommandHandler("digest", digest_command))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    print("Bot started.", flush=True)
    logger.info("Bot started.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
