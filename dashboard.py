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
    """Map a total score + entry count to a pipeline stage label."""
    if entry_count <= 1:
        return "raw"
    if score >= 80:
        return "build"
    if score >= 65:
        return "validated"
    if score >= 50:
        return "refined"
    return "raw"


def _parse_tags(raw):
    """Cluster tags are stored as JSON strings. Be forgiving."""
    if isinstance(raw, list):
        return raw
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return []


# ─── API ─────────────────────────────────────────────────────────────────────

@app.route("/api/stats")
@require_auth
def api_stats():
    """Top-of-dashboard header stats."""
    all_scored = store.get_all_scores()

    with store.get_conn() as conn:
        total_entries = conn.execute(
            "SELECT COUNT(*) AS n FROM entries"
        ).fetchone()["n"]
        total_clusters = conn.execute(
            "SELECT COUNT(*) AS n FROM clusters"
        ).fetchone()["n"]
        new_entries = conn.execute(
            "SELECT COUNT(*) AS n FROM entries "
            "WHERE datetime(created_at) >= datetime('now', '-7 days')"
        ).fetchone()["n"]

    top = all_scored[0] if all_scored else None
    build_count = sum(
        1 for s in all_scored
        if _derive_stage(s["total"], s["entry_count"]) == "build"
    )
    high_density_count = sum(1 for s in all_scored if s["density"] >= 70)

    return jsonify({
        "total_entries": total_entries,
        "total_clusters": total_clusters,
        "new_this_week": new_entries,
        "high_density_clusters": high_density_count,
        "top_score": round(top["total"]) if top else 0,
        "top_score_name": top["name"] if top else "—",
        "build_count": build_count,
    })


@app.route("/api/clusters")
@require_auth
def api_clusters():
    """List all clusters with scores, sorted by total score desc."""
    scored = store.get_all_scores()
    scored_ids = {s["cluster_id"] for s in scored}

    result = []
    for s in scored:
        result.append({
            "id": s["cluster_id"],
            "name": s["name"],
            "summary": s["summary"],
            "tags": _parse_tags(s["tags"]),
            "score": round(s["total"]),
            "stage": _derive_stage(s["total"], s["entry_count"]),
            "entry_count": s["entry_count"],
            "scores": {
                "density": round(s["density"]),
                "revenue_fit": round(s["revenue_fit"]),
                "effort": round(s["effort"]),
                "novelty": round(s["novelty"]),
            },
        })

    # Include unscored clusters at the bottom so nothing is hidden.
    for c in store.get_all_clusters():
        if c["id"] in scored_ids:
            continue
        result.append({
            "id": c["id"],
            "name": c["name"],
            "summary": c["summary"],
            "tags": c["tags"],
            "score": 0,
            "stage": "raw",
            "entry_count": 0,
            "scores": {"density": 0, "revenue_fit": 0, "effort": 0, "novelty": 0},
        })

    return jsonify(result)


@app.route("/api/clusters/<int:cluster_id>")
@require_auth
def api_cluster_detail(cluster_id):
    """Full detail for one cluster: metadata, scores, entries, linked clusters."""
    cluster = store.get_cluster_by_id(cluster_id)
    if not cluster:
        abort(404)

    entries = store.get_cluster_entries(cluster_id)
    links = store.get_cluster_links(cluster_id)

    with store.get_conn() as conn:
        score_row = conn.execute(
            "SELECT * FROM scores WHERE cluster_id = ?", (cluster_id,)
        ).fetchone()

    if score_row:
        scores = {
            "density": round(score_row["density"]),
            "revenue_fit": round(score_row["revenue_fit"]),
            "effort": round(score_row["effort"]),
            "novelty": round(score_row["novelty"]),
            "total": round(score_row["total"]),
            "entry_count": score_row["entry_count"],
            "span_days": round(score_row["span_days"], 1),
            "depth": round(score_row["depth"]),
        }
        stage = _derive_stage(score_row["total"], score_row["entry_count"])
    else:
        scores = {
            "density": 0, "revenue_fit": 0, "effort": 0, "novelty": 0,
            "total": 0, "entry_count": len(entries), "span_days": 0, "depth": 0,
        }
        stage = "raw"

    return jsonify({
        "id": cluster["id"],
        "name": cluster["name"],
        "summary": cluster["summary"],
        "tags": cluster["tags"],
        "stage": stage,
        "scores": scores,
        "entries": [
            {"id": e["id"], "text": e["raw_text"], "created_at": e["created_at"]}
            for e in entries
        ],
        "links": [
            {
                "cluster_id": l["cluster_id"],
                "name": l["name"],
                "summary": l["summary"],
                "reason": l["reason"],
                "strength": l["strength"],
            }
            for l in links
        ],
    })


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    store.init_db()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
