"""Persistent session state, context compaction, and async background memory consolidation."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from agent_app.observability import get_structured_logger, redact_pii

logger = get_structured_logger("geap.memory")


@dataclass
class ConversationTurn:
    """Single turn stored in persistent session state."""

    role: str
    content: str
    estimated_tokens: int


class PersistentSessionStore:
    """SQLite/DB-backed persistent session and memory store across turns."""

    def __init__(self, db_path: str = "agent_sessions.db") -> None:
        self.db_path = db_path
        self._init_schema()

    def _init_schema(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    estimated_tokens INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_bank (
                    session_id TEXT PRIMARY KEY,
                    summary TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def append_turn(self, session_id: str, role: str, content: str) -> ConversationTurn:
        """Persist a redacted turn to the session database."""
        scrubbed = redact_pii(content)
        tokens = max(1, len(scrubbed.split()) * 2)
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO session_turns (session_id, role, content, estimated_tokens, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, role, scrubbed, tokens, now),
            )
            conn.commit()
        return ConversationTurn(role=role, content=scrubbed, estimated_tokens=tokens)

    def get_turns(self, session_id: str) -> list[ConversationTurn]:
        """Load all persisted turns for a session in chronological order."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT role, content, estimated_tokens FROM session_turns WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        return [ConversationTurn(role=r[0], content=r[1], estimated_tokens=r[2]) for r in rows]

    def save_memory_summary(self, session_id: str, summary: str) -> None:
        """Upsert a compacted memory summary into the Memory Bank table."""
        scrubbed = redact_pii(summary)
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO memory_bank (session_id, summary, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    summary = excluded.summary,
                    updated_at = excluded.updated_at
                """,
                (session_id, scrubbed, now),
            )
            conn.commit()

    def get_memory_summary(self, session_id: str) -> str | None:
        """Retrieve the latest consolidated memory summary for a session."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT summary FROM memory_bank WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return row[0] if row else None


def compact_history(
    turns: list[ConversationTurn],
    max_tokens: int = 4000,
    keep_recent: int = 4,
) -> tuple[str, list[ConversationTurn]]:
    """Compact conversation history using a token-budget sliding window + summarization.

    Keeps the most recent ``keep_recent`` turns verbatim while summarizing older turns
    when total tokens exceed ``max_tokens``.
    """
    total_tokens = sum(t.estimated_tokens for t in turns)
    if total_tokens <= max_tokens or len(turns) <= keep_recent:
        return "", turns

    older = turns[:-keep_recent]
    recent = turns[-keep_recent:]
    summary_lines = [f"- {t.role}: {t.content[:160]}" for t in older]
    compacted_summary = "Compacted session context:\n" + "\n".join(summary_lines)
    logger.info(
        "history_compacted",
        extra={
            "structured_fields": {
                "older_turns": len(older),
                "recent_turns": len(recent),
                "original_tokens": total_tokens,
            }
        },
    )
    return compacted_summary, recent


async def consolidate_memory_async(
    store: PersistentSessionStore,
    session_id: str,
    max_tokens: int = 4000,
) -> str:
    """Run non-blocking background memory consolidation for a session."""
    await asyncio.sleep(0)  # Yield to event loop so UI/response is never blocked.
    turns = store.get_turns(session_id)
    summary, _ = compact_history(turns, max_tokens=max_tokens)
    if not summary and turns:
        summary = json.dumps([{"role": t.role, "preview": t.content[:120]} for t in turns[-4:]])
    if summary:
        store.save_memory_summary(session_id, summary)
    return summary


def schedule_background_memory_consolidation(
    store: PersistentSessionStore,
    session_id: str,
    max_tokens: int = 4000,
) -> asyncio.Task[str]:
    """Schedule memory consolidation as a background asyncio task."""
    return asyncio.create_task(consolidate_memory_async(store, session_id, max_tokens=max_tokens))
