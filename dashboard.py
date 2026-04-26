"""
dashboard.py — Web dashboard for Neil's Brain
Registers aiohttp routes for the idea management UI and JSON API.
Designed to be mounted onto the same aiohttp Application the bot already runs,
so bot webhook + dashboard share one server, one port, one SQLite file.

Environment variables:
    DASHBOARD_TOKEN           Secret password for login (REQUIRED)
    DASHBOARD_SECRET          Signing key for session cookies (REQUIRED, 32+ chars)
    DASHBOARD_SECURE_COOKIES  "true" (default) — set to "false" for local HTTP testing

Usage (from bot.py):
    import dashboard
    web_app = web.Application()
    dashboard.setup_routes(web_app)        # <-- mount dashboard
    web_app.router.add_post("/idea", ...)  # your existing Siri webhook
"""

import os
import json
import hmac
import hashlib
from pathlib import Path

from aiohttp import web

import store
from claude_client import process_idea


DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")
DASHBOARD_SECRET = os.environ.get("DASHBOARD_SECRET", "")
SECURE_COOKIES = os.environ.get("DASHBOARD_SECURE_COOKIES", "true").lower() == "true"

COOKIE_NAME = "neilbrain_auth"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days
TEMPLATES_DIR = Path(__file__).parent / "templates"


# ─── Auth helpers ───────────────────────────────────────────────────────────

def _auth_signature() -> str:
    """HMAC-signed value stored in the auth cookie. Changes if SECRET changes."""
    return hmac.new(
        DASHBOARD_SECRET.encode(),
        b"authed:v1",
        hashlib.sha256,
    ).hexdigest()


def _is_authed(request: web.Request) -> bool:
    if not DASHBOARD_SECRET:
        return False
    cookie = request.cookies.get(COOKIE_NAME, "")
    return bool(cookie) and hmac.compare_digest(cookie, _auth_signature())


def require_auth(handler):
    """Decorator: redirect to /login for pages, 401 JSON for /api/* endpoints."""
    async def wrapper(request: web.Request):
        if not _is_authed(request):
            if request.path.startswith("/api/"):
                return web.json_response({"error": "unauthorized"}, status=401)
            return web.HTTPFound("/login")
        return await handler(request)
    return wrapper


# ─── Data helpers ───────────────────────────────────────────────────────────

def _derive_stage(score: float, entry_count: int) -> str:
    """Map total score + entry count to a pipeline stage label."""
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
    """Cluster tags are JSON strings in the DB. Forgiving parser."""
    if isinstance(raw, list):
        return raw
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return []


def _read_template(filename: str) -> str:
    return (TEMPLATES_DIR / filename).read_text(encoding="utf-8")


# ─── Pages ──────────────────────────────────────────────────────────────────

async def index(request: web.Request) -> web.Response:
    if _is_authed(request):
        return web.HTTPFound("/dashboard")
    return web.HTTPFound("/login")


async def login_get(request: web.Request) -> web.Response:
    html = _read_template("login.html").replace("{{ error_block }}", "")
    return web.Response(text=html, content_type="text/html")


async def login_post(request: web.Request) -> web.Response:
    data = await request.post()
    token = data.get("token", "")
    if DASHBOARD_TOKEN and hmac.compare_digest(token, DASHBOARD_TOKEN):
        response = web.HTTPFound("/dashboard")
        response.set_cookie(
            COOKIE_NAME,
            _auth_signature(),
            httponly=True,
            secure=SECURE_COOKIES,
            samesite="Lax",
            max_age=COOKIE_MAX_AGE,
        )
        return response
    error_html = '<div class="error">Invalid token</div>'
    html = _read_template("login.html").replace("{{ error_block }}", error_html)
    return web.Response(text=html, content_type="text/html", status=401)


async def logout(request: web.Request) -> web.Response:
    response = web.HTTPFound("/login")
    response.del_cookie(COOKIE_NAME)
    return response


@require_auth
async def dashboard_page(request: web.Request) -> web.Response:
    html = _read_template("dashboard.html")
    return web.Response(text=html, content_type="text/html")


# ─── API ────────────────────────────────────────────────────────────────────

@require_auth
async def api_stats(request: web.Request) -> web.Response:
    """Header stats for the top of the dashboard."""
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

    return web.json_response({
        "total_entries": total_entries,
        "total_clusters": total_clusters,
        "new_this_week": new_entries,
        "high_density_clusters": high_density_count,
        "top_score": round(top["total"]) if top else 0,
        "top_score_name": top["name"] if top else "—",
        "build_count": build_count,
    })


@require_auth
async def api_clusters(request: web.Request) -> web.Response:
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

    # Append unscored clusters so nothing is hidden.
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

    return web.json_response(result)


@require_auth
async def api_cluster_detail(request: web.Request) -> web.Response:
    """Full detail for one cluster: metadata, scores, entries, linked clusters."""
    try:
        cluster_id = int(request.match_info["cluster_id"])
    except (ValueError, KeyError):
        return web.json_response({"error": "bad id"}, status=400)

    cluster = store.get_cluster_by_id(cluster_id)
    if not cluster:
        return web.json_response({"error": "not found"}, status=404)

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

    return web.json_response({
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


# ─── Capture ────────────────────────────────────────────────────────────────

@require_auth
async def api_capture(request: web.Request) -> web.Response:
    """Accept an idea submitted from the dashboard and process it."""
    try:
        data = await request.json()
        idea_text = data.get("idea", "").strip()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    if not idea_text:
        return web.json_response({"error": "no idea text"}, status=400)

    try:
        reply = await process_idea(idea_text)
        return web.json_response({"ok": True, "reply": reply})
    except Exception as e:
        return web.json_response(
            {"ok": False, "error": str(e)},
            status=500,
        )


# ─── Integration point ──────────────────────────────────────────────────────

def setup_routes(web_app: web.Application) -> None:
    """
    Register dashboard routes on an existing aiohttp Application.
    Call this from bot.py before starting the web server.
    """
    if not DASHBOARD_TOKEN:
        raise RuntimeError("DASHBOARD_TOKEN environment variable must be set")
    if not DASHBOARD_SECRET:
        raise RuntimeError("DASHBOARD_SECRET environment variable must be set")

    web_app.router.add_get("/", index)
    web_app.router.add_get("/login", login_get)
    web_app.router.add_post("/login", login_post)
    web_app.router.add_get("/logout", logout)
    web_app.router.add_get("/dashboard", dashboard_page)
    web_app.router.add_get("/api/stats", api_stats)
    web_app.router.add_get("/api/clusters", api_clusters)
    web_app.router.add_get("/api/clusters/{cluster_id}", api_cluster_detail)
    web_app.router.add_post("/api/capture", api_capture)
