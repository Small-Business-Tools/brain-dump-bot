from backup import run_backup

import sys
sys.stdout.reconfigure(line_buffering=True)

import logging
import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

from claude_client import process_idea
from store import init_db
from digest import send_digest

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", 0))


def is_authorised(update: Update) -> bool:
    if ALLOWED_USER_ID == 0:
        return True
    return update.effective_user.id == ALLOWED_USER_ID


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    await update.message.reply_text(
        "Idea bot is running.\n\n"
        "Send me any idea — text or voice — and I'll capture, tag, and score it.\n\n"
        "Commands:\n"
        "/digest — get your top ideas right now\n"
        "/list — show all idea clusters\n"
        "/help — show this message"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    await start(update, context)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return

    text = update.message.text
    if not text:
        await update.message.reply_text("Send me a text message with your idea.")
        return

    await update.message.reply_text("Got it — processing...")

    try:
        reply = await process_idea(text)
        await update.message.reply_text(reply, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error processing idea: {e}")
        await update.message.reply_text(
            "Something went wrong processing that idea. It has been saved — try /list to check."
        )


async def list_ideas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return

    from store import get_all_clusters
    clusters = get_all_clusters()

    if not clusters:
        await update.message.reply_text("No ideas stored yet. Send me something!")
        return

    lines = ["*Your idea clusters:*\n"]
    for c in clusters[:15]:
        bar = "█" * int(c["score"] / 10) + "░" * (10 - int(c["score"] / 10))
        lines.append(
            f"*{c['name']}*\n"
            f"`{bar}` {c['score']}/100 · {c['entry_count']} entries\n"
            f"_{c['summary']}_\n"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def digest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    await update.message.reply_text("Building your digest...")
    try:
        digest_text = await send_digest()
        await update.message.reply_text(digest_text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Digest error: {e}")
        await update.message.reply_text("Couldn't build digest right now.")


def schedule_weekly_digest(app):
    import datetime
    job_queue = app.job_queue
    job_queue.run_daily(
        lambda ctx: ctx.bot.send_message(
            chat_id=int(os.environ.get("ALLOWED_USER_ID", 0)),
            text="Building your weekly digest...",
        ),
        time=datetime.time(9, 0, 0),
        days=(6,),
        name="weekly_digest"
    )


def main():
    print("Starting bot...", flush=True)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN not set", flush=True)
        sys.exit(1)

    init_db()
    print("Database initialised", flush=True)

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("list", list_ideas))
    app.add_handler(CommandHandler("digest", digest_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    schedule_weekly_digest(app)

    print("Bot started.", flush=True)
    logger.info("Bot started.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
