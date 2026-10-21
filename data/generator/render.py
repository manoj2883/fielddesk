"""Turns a ScenarioSpec into the per-ticket instructions sent to the generator model."""

from __future__ import annotations

from data.generator.spec import ScenarioSpec
from fielddesk.labeling import MachineImpact, WarrantyDecision
from fielddesk.schemas import TrickyType, WarrantyStatus

IMPACT_TEXT = {
    MachineImpact.DOWN: "the machine cannot work, or the dealer has told the customer not to use it",
    MachineImpact.DEGRADED: "the machine still runs but with reduced performance or an intermittent problem",
    MachineImpact.NONE: "there is no current effect on the machine's operation",
}
WARRANTY_TEXT = {
    WarrantyDecision.WITHIN_POLICY: "the machine is within its warranty period, the failure is a normal "
    "component failure, and the failed part is kept for return",
    WarrantyDecision.OUTSIDE_POLICY: "coverage is doubtful: the warranty expired recently, or the dealer is "
    "asking for goodwill, or the cause may be operator damage",
}
STATUS_TEXT = {
    WarrantyStatus.IN_WARRANTY: "within warranty",
    WarrantyStatus.EXPIRED: "warranty expired",
    WarrantyStatus.UNKNOWN: "warranty status unknown to the dealer",
}
TRAIT_TEXT = {
    "typos": "several typos and missing apostrophes",
    "vague": "vague: the dealer leaves out details a reader would want, but the facts are still there",
    "angry": "openly frustrated tone",
    "shorthand": "heavy shorthand and abbreviations",
    "missing_fields": "does not repeat the hours or warranty status in the text",
    "run_on": "one long run-on paragraph",
}


def render(spec: ScenarioSpec, attempt: int = 0) -> str:
    f, s = spec.facts, spec.surface
    lines = ["FACTS"]
    lines.append(
        f"- Machine: {s.machine_model} ({s.machine_type}), about {s.machine_hours_true} hours."
    )
    lines.append(f"- Main ask: {s.ask_details[0]}.")
    for detail in s.ask_details[1:]:
        lines.append(f"- Second ask, also included: {detail}.")
    if f.conflicting_asks:
        lines.append(
            "- The two asks pull against each other (e.g. act now vs. wait for a decision); "
            "the dealer wants both and does not say which comes first."
        )
    if f.machine_impact is not MachineImpact.NONE:
        # "No impact" is conveyed by silence: dealers don't announce that nothing is broken.
        lines.append(f"- Machine impact: {IMPACT_TEXT[f.machine_impact]}.")
    if s.error_code:
        lines.append(f"- Error code on the display: {s.error_code} ({s.error_meaning}).")
    if f.safety_code and not f.safety_symptom:
        lines.append(
            "- The dealer says in plain words what the code is about (e.g. 'park brake "
            "fault', 'steering pressure warning') but may underplay how serious it is."
        )
    if s.safety_symptom_text:
        lines.append(f"- Safety hazard to describe: {s.safety_symptom_text}.")
    if s.deadline_text:
        lines.append(f"- Time pressure: {s.deadline_text}.")
    if f.warranty_decision is not WarrantyDecision.NONE:
        lines.append(f"- Coverage situation: {WARRANTY_TEXT[f.warranty_decision]}.")
    if f.tricky_type is TrickyType.WRONG_FIELD:
        lines.append(
            f"- The text should make clear the machine is {STATUS_TEXT[s.warranty_status_true]} "
            "(the form field was filled in wrongly; do not mention the form)."
        )
    if f.tricky_type is TrickyType.WARRANTY_IN_FAULT:
        lines.append("- The coverage question is secondary: a single sentence near the end.")
    if s.misleading_hint:
        lines.append(f"- Wording twist: {s.misleading_hint}.")
    if s.injection_text:
        lines.append(
            f'- Include this sentence verbatim somewhere in the message: "{s.injection_text}"'
        )

    lo, hi = s.length_words
    lines += [
        "",
        "STYLE",
        f"- Dealer: {s.dealer}. Voice: {s.voice} ({s.voice_notes}).",
        f"- Message length: {lo}-{hi} words.",
    ]
    for t in s.messy_traits:
        lines.append(f"- {TRAIT_TEXT[t]}.")
    if attempt:
        lines.append(
            f"- Retry {attempt}: the previous version broke rule 6 or rule 7; follow them strictly."
        )
    return "\n".join(lines)
