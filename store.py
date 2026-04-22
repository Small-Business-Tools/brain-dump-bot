import sqlite3
import os
import json
from datetime import datetime

DB_PATH = os.environ.get("DB_PATH", "ideas.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist. Safe to call on every startup."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS entries (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                raw_text    TEXT NOT NULL,
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS clusters (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                summary     TEXT NOT NULL,
                tags        TEXT NOT NULL DEFAULT '[]',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cluster_entries (
                cluster_id  INTEGER REFERENCES clusters(id),
                entry_id    INTEGER REFERENCES entries(id),
                PRIMARY KEY (cluster_id, entry_id)
            );

            CREATE TABLE IF NOT EXISTS scores (
                cluster_id      INTEGER PRIMARY KEY REFERENCES clusters(id),
                density         REAL NOT NULL DEFAULT 0,
                revenue_fit     REAL NOT NULL DEFAULT 0,
                effort          REAL NOT NULL DEFAULT 0,
                novelty         REAL NOT NULL DEFAULT 0,
                total           REAL NOT NULL DEFAULT 0,
                entry_count     INTEGER NOT NULL DEFAULT 0,
                span_days       REAL NOT NULL DEFAULT 0,
                depth           REAL NOT NULL DEFAULT 0,
                updated_at      TEXT NOT NULL
            );

            -- Stores cross-cluster relationships discovered at entry time.
            -- cluster_a is always the lower ID to prevent duplicate pairs.
            CREATE TABLE IF NOT EXISTS cluster_links (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                cluster_a   INTEGER NOT NULL REFERENCES clusters(id),
                cluster_b   INTEGER NOT NULL REFERENCES clusters(id),
                reason      TEXT,
                strength    INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                UNIQUE(cluster_a, cluster_b),
                CHECK(cluster_a < cluster_b)
            );
        """)


# ─── Entries ────────────────────────────────────────────────────────────────

def save_entry(raw_text: str) -> int:
    """Save a raw idea entry and return its ID."""
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO entries (raw_text, created_at) VALUES (?, ?)",
            (raw_text, now)
        )
        return cur.lastrowid


# ─── Clusters ───────────────────────────────────────────────────────────────

def save_cluster(name: str, summary: str, tags: list) -> int:
    """Create a new idea cluster and return its ID."""
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO clusters (name, summary, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (name, summary, json.dumps(tags), now, now)
        )
        return cur.lastrowid


def update_cluster(cluster_id: int, name: str, summary: str, tags: list):
    """Update an existing cluster's metadata."""
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE clusters SET name=?, summary=?, tags=?, updated_at=? WHERE id=?",
            (name, summary, json.dumps(tags), now, cluster_id)
        )


def get_all_clusters() -> list[dict]:
    """Return all clusters as a list of dicts (for Claude's context)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, summary, tags FROM clusters ORDER BY updated_at DESC"
        ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "summary": r["summary"],
            "tags": json.loads(r["tags"]),
        }
        for r in rows
    ]


def get_cluster_by_id(cluster_id: int) -> dict | None:
    """Return a single cluster by ID, or None if not found."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, summary, tags FROM clusters WHERE id=?",
            (cluster_id,)
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "summary": row["summary"],
        "tags": json.loads(row["tags"]),
    }


def link_entry_to_cluster(cluster_id: int, entry_id: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO cluster_entries (cluster_id, entry_id) VALUES (?, ?)",
            (cluster_id, entry_id)
        )


def get_cluster_entries(cluster_id: int) -> list[dict]:
    """Return all raw entries for a cluster (used for density scoring)."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT e.id, e.raw_text, e.created_at
            FROM entries e
            JOIN cluster_entries ce ON ce.entry_id = e.id
            WHERE ce.cluster_id = ?
            ORDER BY e.created_at ASC
            """,
            (cluster_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ─── Cluster links ───────────────────────────────────────────────────────────

def save_cluster_link(cluster_a: int, cluster_b: int, reason: str = "") -> bool:
    """
    Persist a directional link between two clusters.

    IDs are normalised so cluster_a is always the lower value, preventing
    duplicate pairs regardless of which direction Claude discovers them.

    Returns True if a new link was created, False if it already existed
    (in which case the strength counter is incremented).
    """
    a, b = (cluster_a, cluster_b) if cluster_a < cluster_b else (cluster_b, cluster_a)
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id, strength FROM cluster_links WHERE cluster_a=? AND cluster_b=?",
            (a, b)
        ).fetchone()
        if existing:
            # Link already known — strengthen it
            conn.execute(
                "UPDATE cluster_links SET strength=?, reason=?, updated_at=? WHERE id=?",
                (existing["strength"] + 1, reason or None, now, existing["id"])
            )
            return False
        else:
            conn.execute(
                "INSERT INTO cluster_links (cluster_a, cluster_b, reason, strength, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (a, b, reason or None, now, now)
            )
            return True


def get_cluster_links(cluster_id: int) -> list[dict]:
    """
    Return all clusters linked to the given cluster, with reason and strength.
    Looks up both sides of the relationship (cluster_a OR cluster_b).
    """
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                cl.id        AS link_id,
                cl.reason,
                cl.strength,
                cl.created_at,
                CASE WHEN cl.cluster_a = ? THEN cl.cluster_b ELSE cl.cluster_a END AS other_id
            FROM cluster_links cl
            WHERE cl.cluster_a = ? OR cl.cluster_b = ?
            ORDER BY cl.strength DESC, cl.created_at DESC
            """,
            (cluster_id, cluster_id, cluster_id)
        ).fetchall()

    results = []
    for row in rows:
        cluster = get_cluster_by_id(row["other_id"])
        if cluster:
            results.append({
                "link_id": row["link_id"],
                "cluster_id": cluster["id"],
                "name": cluster["name"],
                "summary": cluster["summary"],
                "reason": row["reason"],
                "strength": row["strength"],
            })
    return results


def get_strongest_links(limit: int = 10) -> list[dict]:
    """Return the highest-strength links across the whole graph (useful for digest)."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT cl.cluster_a, cl.cluster_b, cl.reason, cl.strength,
                   ca.name AS name_a, cb.name AS name_b
            FROM cluster_links cl
            JOIN clusters ca ON ca.id = cl.cluster_a
            JOIN clusters cb ON cb.id = cl.cluster_b
            ORDER BY cl.strength DESC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ─── Scores ─────────────────────────────────────────────────────────────────

def save_scores(cluster_id: int, scores: dict):
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO scores
                (cluster_id, density, revenue_fit, effort, novelty, total,
                 entry_count, span_days, depth, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cluster_id) DO UPDATE SET
                density=excluded.density,
                revenue_fit=excluded.revenue_fit,
                effort=excluded.effort,
                novelty=excluded.novelty,
                total=excluded.total,
                entry_count=excluded.entry_count,
                span_days=excluded.span_days,
                depth=excluded.depth,
                updated_at=excluded.updated_at
            """,
            (
                cluster_id,
                scores.get("density", 0),
                scores.get("revenue_fit", 0),
                scores.get("effort", 0),
                scores.get("novelty", 0),
                scores.get("total", 0),
                scores.get("entry_count", 0),
                scores.get("span_days", 0),
                scores.get("depth", 0),
                now,
            )
        )


def get_all_scores() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT s.*, c.name
            FROM scores s
            JOIN clusters c ON c.id = s.cluster_id
            ORDER BY s.total DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


# ─── Fallback ────────────────────────────────────────────────────────────────

def get_or_create_fallback_cluster() -> int:
    """Return (or create) the 'Unprocessed Ideas' cluster for error recovery."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM clusters WHERE name='Unprocessed Ideas'"
        ).fetchone()
        if row:
            return row["id"]
    return save_cluster(
        "Unprocessed Ideas",
        "Ideas that could not be processed automatically.",
        ["unprocessed"]
    )
