from backup import run_backup
import sys
sys.stdout.reconfigure(line_buffering=True)

import logging
import os
import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from aiohttp import web
import asyncio

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
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
PORT = int(os.environ.get("PORT", 8080))


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
        "/backup — back up your database to GitHub now\n"
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


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    await update.message.reply_text("Starting backup...")
    from backup import backup_database
    result = await backup_database()
    await update.message.reply_text(result)


# --- Siri webhook endpoint ---

async def webhook_handler(request: web.Request) -> web.Response:
    """Accepts incoming idea from Siri Shortcut via POST request."""
    try:
        secret = request.headers.get("X-Secret", "")
        if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
            logger.warning("Webhook request with invalid secret")
            return web.Response(status=401, text="Unauthorised")

        data = await request.json()
        text = data.get("idea", "").strip()

        if not text:
            return web.Response(status=400, text="No idea text provided")

        logger.info(f"Webhook received idea: {text[:50]}...")

        # Process in background so we can return quickly to Siri
        asyncio.create_task(handle_webhook_idea(text, request.app["bot"]))

        return web.Response(status=200, text="Idea received — processing now.")

    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return web.Response(status=500, text="Internal error")


async def handle_webhook_idea(text: str, bot):
    """Process the idea and send Telegram confirmation."""
    try:
        reply = await process_idea(text)
        if ALLOWED_USER_ID:
            await bot.send_message(
                chat_id=ALLOWED_USER_ID,
                text=f"📱 *Via Siri:*\n{reply}",
                parse_mode="Markdown"
            )
    except Exception as e:
        logger.error(f"Webhook idea processing error: {e}")
        if ALLOWED_USER_ID:
            await bot.send_message(
                chat_id=ALLOWED_USER_ID,
                text="Siri idea was saved but couldn't be processed right now. Check /list."
            )


def create_web_app(bot) -> web.Application:
    app = web.Application()
    app["bot"] = bot
    app.router.add_post("/idea", webhook_handler)
    app.router.add_get("/health", lambda r: web.Response(text="OK"))
    return app


def schedule_jobs(app):
    job_queue = app.job_queue

    # Weekly digest every Sunday at 09:00 UTC
    job_queue.run_daily(
        run_backup,
        time=datetime.time(9, 0, 0),
        days=(6,),
        name="weekly_digest"
    )

    # Daily backup at 03:00 UTC
    job_queue.run_daily(
        run_backup,
        time=datetime.time(3, 0, 0),
        days=(0, 1, 2, 3, 4, 5, 6),
        name="daily_backup"
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
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    schedule_jobs(app)

    # Start the web server alongside the bot
    web_app = create_web_app(app.bot)
    runner = web.AppRunner(web_app)

    async def run():
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        print(f"Webhook server running on port {PORT}", flush=True)
        print("Bot started.", flush=True)
        logger.info("Bot started.")
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        # Keep running
        await asyncio.Event().wait()

    asyncio.run(run())


if __name__ == "__main__":
    main()
