import os
import base64
import logging
import sqlite3
from datetime import datetime
import httpx

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", "ideas.db")
GITHUB_TOKEN = os.environ.get("GITHUB_BACKUP_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_BACKUP_REPO")
GITHUB_API = "https://api.github.com"


async def backup_database() -> str:
    """
    Read the SQLite database, base64 encode it, and push to GitHub.
    Returns a status message.
    """
    if not GITHUB_TOKEN or not GITHUB_REPO:
        logger.error("Backup skipped: GITHUB_BACKUP_TOKEN or GITHUB_BACKUP_REPO not set")
        return "Backup skipped — GitHub credentials not configured."

    if not os.path.exists(DB_PATH):
        logger.warning("Backup skipped: database file not found")
        return "Backup skipped — no database file found yet."

    try:
        # Read and encode the database file
        with open(DB_PATH, "rb") as f:
            content = base64.b64encode(f.read()).decode("utf-8")

        now = datetime.utcnow()
        filename = f"backups/ideas_{now.strftime('%Y-%m-%d_%H-%M-%S')}.db"
        commit_message = f"Backup {now.strftime('%Y-%m-%d %H:%M')} UTC"

        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        }

        payload = {
            "message": commit_message,
            "content": content,
        }

        url = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{filename}"

        async with httpx.AsyncClient() as client:
            response = await client.put(url, json=payload, headers=headers, timeout=30)

        if response.status_code in (200, 201):
            logger.info(f"Backup successful: {filename}")
            return f"Backup complete — saved to `{filename}`"
        else:
            logger.error(f"Backup failed: {response.status_code} {response.text}")
            return f"Backup failed — GitHub returned {response.status_code}."

    except Exception as e:
        logger.error(f"Backup error: {e}")
        return f"Backup failed — {type(e).__name__}: {e}"


async def run_backup(context):
    """Job queue entry point — called by the scheduler."""
    msg = await backup_database()
    logger.info(f"Backup job result: {msg}")
    # Optionally notify yourself on Telegram
    try:
        chat_id = int(os.environ.get("ALLOWED_USER_ID", 0))
        if chat_id:
            await context.bot.send_message(chat_id=chat_id, text=f"🗄 {msg}")
    except Exception as e:
        logger.error(f"Could not send backup notification: {e}")
