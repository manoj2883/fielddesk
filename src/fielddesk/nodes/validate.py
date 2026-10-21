"""validate: no LLM. Normalizes fields, records what is missing, runs the deterministic guards."""

from __future__ import annotations

import re
import time
from typing import Any

from fielddesk.guards import detect_injection, detect_safety_terms
from fielddesk.llm import log_event
from fielddesk.schemas import NodeMetrics, TicketInput, WarrantyStatus
from fielddesk.state import TriageState

_CODE_SHAPE = re.compile(r"^[A-Z]{1,4}-?[A-Z]?\d{2,}$")


def normalize(ticket: TicketInput) -> TicketInput:
    code = ticket.error_code.upper().replace(" ", "") if ticket.error_code else None
    return ticket.model_copy(
        update={
            "machine_model": " ".join(ticket.machine_model.split()).title(),
            "dealer_name": " ".join(ticket.dealer_name.split()),
            "error_code": code,
        }
    )


def missing_info(ticket: TicketInput) -> list[str]:
    missing = []
    if ticket.machine_hours is None:
        missing.append("machine_hours")
    if ticket.warranty_status is WarrantyStatus.UNKNOWN:
        missing.append("warranty_status")
    if ticket.error_code is None:
        missing.append("error_code")
    elif not _CODE_SHAPE.match(ticket.error_code):
        missing.append("error_code_unrecognized_format")
    return missing


async def validate(state: TriageState) -> dict[str, Any]:
    started = time.perf_counter()
    ticket = normalize(state["ticket"])
    text = f"{ticket.subject}\n{ticket.message}"
    update = {
        "ticket": ticket,
        "missing_info": missing_info(ticket),
        "safety_terms": detect_safety_terms(text),
        "injection_markers": detect_injection(text),
    }
    latency = round((time.perf_counter() - started) * 1000, 2)
    log_event(
        "node",
        run_id=state["run_id"],
        node="validate",
        latency_ms=latency,
        safety_terms=update["safety_terms"],
        injection_markers=update["injection_markers"],
    )
    return {**update, "metrics": [NodeMetrics(node="validate", latency_ms=latency)]}
