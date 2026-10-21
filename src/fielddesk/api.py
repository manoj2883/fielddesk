"""HTTP API. Every response carries ``X-Request-ID``; errors share one JSON shape and never include
stack traces, inputs or secrets."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from fielddesk.config import Settings, get_settings
from fielddesk.graph import TriageAgent
from fielddesk.llm import log_event
from fielddesk.schemas import TicketInput, TriageResult
from fielddesk.store import RunStore

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _error(status: int, kind: str, message: str, request: Request, **extra: Any) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    body = {"error": {"type": kind, "message": message, **extra}, "request_id": request_id}
    return JSONResponse(status_code=status, content=body)


def create_app(
    settings: Settings | None = None,
    agent: TriageAgent | None = None,
    store: RunStore | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        s = settings or get_settings()
        app.state.settings = s
        app.state.agent = agent or TriageAgent(s)
        app.state.store = store or RunStore(s.db_path)
        await app.state.store.init()
        yield

    app = FastAPI(title="FieldDesk", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request.state.request_id = incoming if _REQUEST_ID.match(incoming) else uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def on_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Only location + message: pydantic's "input" would echo ticket text back.
        details = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return _error(422, "validation_error", "request body is invalid", request, details=details)

    @app.exception_handler(StarletteHTTPException)
    async def on_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error(exc.status_code, "http_error", str(exc.detail), request)

    @app.exception_handler(Exception)
    async def on_unhandled(request: Request, exc: Exception) -> JSONResponse:
        log_event("api_error", request_id=request.state.request_id, error=type(exc).__name__)
        return _error(500, "internal_error", "internal error", request)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready(request: Request) -> JSONResponse:
        s: Settings = request.app.state.settings
        db_ok = await request.app.state.store.writable()
        checks = {
            "config_loaded": True,
            "db_writable": db_ok,
            "anthropic_key_configured": s.anthropic_api_key is not None,
        }
        status = 200 if db_ok else 503
        return JSONResponse(status_code=status, content={"ready": db_ok, "checks": checks})

    @app.post("/triage", response_model=TriageResult)
    async def triage(ticket: TicketInput, request: Request) -> TriageResult:
        result: TriageResult = await request.app.state.agent.run(ticket)
        await request.app.state.store.save(ticket, result)
        log_event(
            "triage",
            request_id=request.state.request_id,
            run_id=str(result.run_id),
            ticket_id=ticket.ticket_id,
            escalate=result.escalate,
        )
        return result

    @app.get("/runs/{ticket_id}", response_model=TriageResult | list[TriageResult])
    async def runs(
        ticket_id: str,
        request: Request,
        all_runs: Annotated[bool, Query(alias="all")] = False,
    ) -> TriageResult | list[TriageResult]:
        store: RunStore = request.app.state.store
        if all_runs:
            history = await store.history(ticket_id)
            if not history:
                raise HTTPException(status_code=404, detail=f"no runs for ticket {ticket_id}")
            return history
        latest = await store.latest(ticket_id)
        if latest is None:
            raise HTTPException(status_code=404, detail=f"no runs for ticket {ticket_id}")
        return latest

    return app


app = create_app()
