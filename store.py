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
    """Create tables if they don't exist."""
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
        """)


def save_entry(raw_text: str) -> int:
    """Save a raw idea entry and return its ID."""
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO entries (raw_text, created_at) VALUES (?, ?)",
            (raw_text, now)
        )
        return cur.lastrowid


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
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE clusters SET name=?, summary=?, tags=?, updated_at=? WHERE id=?",
            (name, summary, json.dumps(tags), now, cluster_id)
        )


def link_entry_to_cluster(cluster_id: int, entry_id: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO cluster_entries (cluster_id, entry_id) VALUES (?, ?)",
            (cluster_id, entry_id)
        )


def save_scores(cluster_id: int, scores: dict):
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO scores
                (cluster_id, density, revenue_fit, effort, novelty, total, entry_count, span_days, depth, updated_at)
            VALUES
                (:cluster_id, :density, :revenue_fit, :effort, :novelty, :total, :entry_count, :span_days, :depth, :updated_at)
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
        """, {**scores, "cluster_id": cluster_id, "updated_at": now})


def get_all_clusters() -> list[dict]:
    """Return all clusters with their latest scores, sorted by total score."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT c.id, c.name, c.summary, c.tags, c.created_at, c.updated_at,
                   COALESCE(s.total, 0) as score,
                   COALESCE(s.entry_count, 0) as entry_count,
                   COALESCE(s.density, 0) as density
            FROM clusters c
            LEFT JOIN scores s ON s.cluster_id = c.id
            ORDER BY score DESC
        """).fetchall()
        return [dict(r) for r in rows]


def get_cluster_entries(cluster_id: int) -> list[dict]:
    """Return all raw entries for a cluster, oldest first."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT e.id, e.raw_text, e.created_at
            FROM entries e
            JOIN cluster_entries ce ON ce.entry_id = e.id
            WHERE ce.cluster_id = ?
            ORDER BY e.created_at ASC
        """, (cluster_id,)).fetchall()
        return [dict(r) for r in rows]


def get_top_clusters(n: int = 5) -> list[dict]:
    """Return top N clusters by total score."""
    clusters = get_all_clusters()
    return clusters[:n]
