"""
dashboard.py — Web dashboard for Neil's Brain
Serves the idea management UI and JSON API endpoints over Flask.
Reads directly from the same SQLite DB the Telegram bot writes to.

Environment variables:
    DASHBOARD_TOKEN    Secret token for login (REQUIRED)
    DASHBOARD_SECRET   Flask session secret key (REQUIRED)
    DB_PATH            Path to SQLite DB (default: ideas.db — same as bot)
    PORT               Port to serve on (default: 8080)

Run locally:
    DASHBOARD_TOKEN=mysecret DASHBOARD_SECRET=whatever python dashboard.py
"""

import os
import json
from functools import wraps

from flask import (
    Flask, request, render_template, jsonify,
    redirect, url_for, session, abort
)

import store


app = Flask(__name__)
app.secret_key = os.environ.get("DASHBOARD_SECRET", "")
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")

if not DASHBOARD_TOKEN:
    raise RuntimeError("DASHBOARD_TOKEN environment variable must be set")
if not app.secret_key:
    raise RuntimeError("DASHBOARD_SECRET environment variable must be set")


# ─── Auth ────────────────────────────────────────────────────────────────────

def require_auth(fn):
    """Require a valid authenticated session for a route."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("authed"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        token = request.form.get("token", "")
        if token == DASHBOARD_TOKEN:
            session["authed"] = True
            session.permanent = True
            return redirect(url_for("dashboard"))
        error = "Invalid token"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ─── Pages ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    if session.get("authed"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/dashboard")
@require_auth
def dashboard():
    return render_template("dashboard.html")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _derive_stage(score: float, entry_count: int) -> str:
    """Map a total score + entry cou
