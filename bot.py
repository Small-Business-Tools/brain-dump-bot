# ---------------------------------------------------------------------------
# App startup
# ---------------------------------------------------------------------------

async def run():
    init_db()

    # Telegram bot
    tg_app = ApplicationBuilder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()
    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(CommandHandler("help", help_command))
    tg_app.add_handler(CommandHandler("list", list_ideas))
    tg_app.add_handler(CommandHandler("digest", digest_command))
    tg_app.add_handler(CommandHandler("backup", backup_command))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    tg_app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    # aiohttp web server for Siri Shortcut webhook
    web_app = web.Application()
    web_app.router.add_post("/idea", handle_webhook)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Webhook server listening on port {PORT}")

    # Run both concurrently
    print("Bot started.", flush=True)
    logger.info("Bot started.")
    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
