from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fielddesk.api import create_app
from fielddesk.config import Settings
from fielddesk.schemas import Category, EscalationReason, TicketInput, TriageResult
from fielddesk.store import RunStore

BODY = {
    "ticket_id": "T-77",
    "dealer_name": "Coldwater Flats Implement",
    "machine_model": "Ridgeline T680",
    "subject": "Need a shift solenoid",
    "message": "Diagnosed RX-E214, need the solenoid.",
}


class FakeAgent:
    def __init__(self) -> None:
        self.calls = 0
        self.explode = False

    async def run(self, ticket: TicketInput) -> TriageResult:
        self.calls += 1
        if self.explode:
            raise RuntimeError("secret internals sk-ant-should-not-leak")
        return TriageResult(
            ticket_id=ticket.ticket_id,
            predicted_category=Category.PARTS_REQUEST,
            escalate=self.calls % 2 == 0,
            escalation_reason=EscalationReason.LOW_CONFIDENCE if self.calls % 2 == 0 else None,
            draft_reply=None if self.calls % 2 == 0 else f"draft {self.calls}",
        )


@pytest.fixture
def client(tmp_path: Path) -> Iterator[tuple[TestClient, FakeAgent]]:
    agent = FakeAgent()
    settings = Settings(anthropic_api_key=None, db_path=tmp_path / "runs.db")
    app = create_app(settings=settings, agent=agent, store=RunStore(settings.db_path))  # type: ignore[arg-type]
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c, agent


def test_health_and_ready(client: tuple[TestClient, Any]) -> None:
    c, _ = client
    assert c.get("/health").json() == {"status": "ok"}
    ready = c.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"] == {
        "config_loaded": True,
        "db_writable": True,
        "anthropic_key_configured": False,
    }


def test_triage_persists_every_run(client: tuple[TestClient, Any]) -> None:
    c, _ = client
    first = c.post("/triage", json=BODY).json()
    second = c.post("/triage", json=BODY).json()
    assert first["run_id"] != second["run_id"]

    latest = c.get("/runs/T-77").json()
    assert latest["run_id"] == second["run_id"]
    history = c.get("/runs/T-77", params={"all": "true"}).json()
    assert [r["run_id"] for r in history] == [second["run_id"], first["run_id"]]


def test_request_id_is_echoed_or_generated(client: tuple[TestClient, Any]) -> None:
    c, _ = client
    assert (
        c.get("/health", headers={"X-Request-ID": "abc-123"}).headers["X-Request-ID"] == "abc-123"
    )
    generated = c.get("/health", headers={"X-Request-ID": "bad id\n"}).headers["X-Request-ID"]
    assert len(generated) == 32


def test_validation_error_shape_does_not_echo_input(client: tuple[TestClient, Any]) -> None:
    c, _ = client
    r = c.post("/triage", json={**BODY, "message": "x" * 5000, "machine_hours": -3})
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["type"] == "validation_error"
    assert body["request_id"] == r.headers["X-Request-ID"]
    assert "xxxx" not in r.text


def test_unknown_ticket_is_404(client: tuple[TestClient, Any]) -> None:
    c, _ = client
    r = c.get("/runs/nope")
    assert r.status_code == 404
    assert r.json()["error"]["type"] == "http_error"
    assert c.get("/runs/nope", params={"all": "true"}).status_code == 404


def test_unhandled_error_is_generic(client: tuple[TestClient, Any]) -> None:
    c, agent = client
    agent.explode = True
    r = c.post("/triage", json=BODY)
    assert r.status_code == 500
    assert r.json()["error"] == {"type": "internal_error", "message": "internal error"}
    assert "sk-ant" not in r.text
    assert "Traceback" not in r.text


async def test_store_readiness_fails_on_unwritable_path(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "missing-dir" / "x" / "runs.db")
    assert await store.writable() is False
