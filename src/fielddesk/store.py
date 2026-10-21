"""SQLite persistence for triage runs. Every run is kept, so a ticket's history is auditable."""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from fielddesk.schemas import SCHEMA_VERSION, TicketInput, TriageResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id         TEXT PRIMARY KEY,
    ticket_id      TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    escalate       INTEGER NOT NULL,
    ticket_json    TEXT NOT NULL,
    result_json    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_by_ticket ON runs (ticket_id, created_at);
"""


class RunStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def save(self, ticket: TicketInput, result: TriageResult) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(result.run_id),
                    result.ticket_id,
                    result.created_at.isoformat(),
                    SCHEMA_VERSION,
                    int(result.escalate),
                    ticket.model_dump_json(),
                    result.model_dump_json(),
                ),
            )
            await db.commit()

    async def history(self, ticket_id: str) -> list[TriageResult]:
        """All runs for a ticket, newest first."""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT result_json FROM runs WHERE ticket_id = ? "
                "ORDER BY created_at DESC, rowid DESC",
                (ticket_id,),
            )
            rows = await cursor.fetchall()
        return [TriageResult.model_validate_json(row[0]) for row in rows]

    async def latest(self, ticket_id: str) -> TriageResult | None:
        runs = await self.history(ticket_id)
        return runs[0] if runs else None

    async def writable(self) -> bool:
        """Readiness probe: can we open a write transaction? Rolls back, leaves no trace."""
        try:
            async with aiosqlite.connect(self.path) as db:
                await db.execute("BEGIN IMMEDIATE")
                await db.execute("SELECT 1 FROM runs LIMIT 1")
                await db.rollback()
            return True
        except Exception:
            return False
