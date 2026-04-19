import sys
print("Python version:", sys.version, flush=True)
print("Importing os...", flush=True)
import os
print("Importing telegram...", flush=True)
from telegram.ext import ApplicationBuilder
print("Importing anthropic...", flush=True)
import anthropic
print("All imports OK", flush=True)
print("TELEGRAM_BOT_TOKEN set:", bool(os.environ.get("TELEGRAM_BOT_TOKEN")), flush=True)
print("ANTHROPIC_API_KEY set:", bool(os.environ.get("ANTHROPIC_API_KEY")), flush=True)
print("ALLOWED_USER_ID:", os.environ.get("ALLOWED_USER_ID"), flush=True)
print("DB_PATH:", os.environ.get("DB_PATH"), flush=True)
