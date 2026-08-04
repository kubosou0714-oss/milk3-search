"""AIおすすめ紹介文の SQLite キャッシュ。"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

_LOCK = threading.Lock()

IS_VERCEL = os.environ.get("VERCEL") == "1"
BASE_DIR = Path(__file__).resolve().parent


def _db_path() -> Path:
    override = os.environ.get("AI_BLURB_DB", "").strip()
    if override:
        return Path(override)
    if IS_VERCEL:
        return Path("/tmp/ai_blurbs.db")
    path = BASE_DIR / "data" / "ai_blurbs.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), timeout=10, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_blurbs (
            work_key TEXT PRIMARY KEY,
            blurb TEXT NOT NULL,
            model TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()
    return conn


def get_blurb(work_key: str) -> str | None:
    key = (work_key or "").strip()
    if not key:
        return None
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT blurb FROM ai_blurbs WHERE work_key = ?",
                (key,),
            ).fetchone()
            return row[0] if row and row[0] else None
        finally:
            conn.close()


def save_blurb(work_key: str, blurb: str, model: str = "") -> None:
    key = (work_key or "").strip()
    text = (blurb or "").strip()
    if not key or not text:
        return
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO ai_blurbs (work_key, blurb, model)
                VALUES (?, ?, ?)
                ON CONFLICT(work_key) DO UPDATE SET
                    blurb = excluded.blurb,
                    model = excluded.model,
                    created_at = datetime('now')
                """,
                (key, text, model or ""),
            )
            conn.commit()
        finally:
            conn.close()
