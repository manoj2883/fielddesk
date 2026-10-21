"""Concrete situations for each ask, so tickets read like real requests instead of definitions.

The first sample (spec_v1) gave the generator only category definitions ("needs an invoice,
credit, statement or payment issue corrected"). The model echoed them back verbatim, which read
badly and would let a keyword baseline solve the category for free (JOURNAL 2026-10-21).
Each ask now carries one specific situation. Nothing here affects a label.
"""

from __future__ import annotations

import random

from fielddesk.schemas import Category

# Parts by subsystem, so a part request matches the machine's error code when there is one.
PARTS: dict[str, tuple[str, ...]] = {
    "engine": (
        "a fuel rail pressure sensor",
        "a coolant thermostat housing",
        "an exhaust temperature sensor",
    ),
    "transmission": (
        "a shift solenoid",
        "a clutch pack friction kit",
        "a transmission speed sensor",
    ),
    "hydraulics": ("a remote valve section", "a boom hold valve", "a hydraulic oil cooler"),
    "electrical": ("a display wiring harness", "an alternator", "a main relay module"),
    "steering": ("a steering pressure sensor", "a steering control valve"),
    "header": ("a header height sensor", "a feeder house drive belt"),
    "threshing": ("a rotor speed sensor", "a set of concave bolts"),
    "grain_handling": ("an unloading auger gearbox", "a chaff deflector panel"),
    "brakes": ("a set of brake pads", "a brake master cylinder"),
    "cab": ("an HVAC compressor clutch", "a cab air filter element"),
    "telematics": ("a telematics modem",),
}
SUBSYSTEMS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "tractor": ("engine", "transmission", "hydraulics", "electrical", "steering", "brakes", "cab"),
    "combine": (
        "engine",
        "hydraulics",
        "electrical",
        "header",
        "threshing",
        "grain_handling",
        "cab",
    ),
    "loader": ("engine", "transmission", "hydraulics", "electrical", "steering", "brakes", "cab"),
}
SYMPTOMS: dict[str, tuple[str, ...]] = {
    "engine": ("hard starting once warm", "losing power under load", "running hot on long days"),
    "transmission": ("hesitating on upshifts", "dropping out of gear on hills"),
    "hydraulics": ("slow lift on the remotes", "implement drifting down when parked"),
    "electrical": ("display going blank at random", "batteries dead every morning"),
    "steering": ("steering feels notchy at low speed", "guidance wandering off line"),
    "header": ("header bouncing at normal speed", "feeder house slipping in heavy crop"),
    "threshing": ("rotor speed swinging", "cracked grain in the tank"),
    "grain_handling": ("auger stalling when unloading", "grain loss higher than normal"),
    "brakes": ("brakes squealing", "pedal travel longer than it used to be"),
    "cab": ("AC blowing warm air", "cab filling with dust"),
    "telematics": ("machine not showing up on the fleet portal",),
}
SCHEDULING: tuple[str, ...] = (
    "wants a factory pre-season inspection for three of the same model on one day",
    "wants a slot for a factory software campaign update",
    "wants operator training for a first-time customer on delivery day",
    "needs to move an already-booked factory technician visit to the following week",
    "wants a factory technician to come out for a repeat problem the dealer's techs keep chasing",
    "wants to book a service-school seat for a new technician",
)
BILLING: tuple[str, ...] = (
    "the credit for returned parts was never applied to the statement",
    "freight was charged twice on one order",
    "a warranty labor reimbursement was paid at the wrong hourly rate",
    "the statement shows an invoice that was already paid",
    "the core charge was not refunded after the core was sent back",
    "a parts invoice lists the wrong quantity",
)
OTHER: tuple[str, ...] = (
    "asks for the operator's manual in another language",
    "asks how to transfer machine ownership records to a new customer",
    "asks whether Ridgeline will attend a regional farm show",
    "complains about a rude phone call but asks for nothing specific",
    "asks how to reset the dealer portal password",
    "asks for paint codes to touch up a scratched hood",
)


def subsystem_for(machine_type: str, code_subsystem: str | None, rng: random.Random) -> str:
    if code_subsystem and code_subsystem in PARTS:
        return code_subsystem
    return rng.choice(SUBSYSTEMS_BY_TYPE[machine_type])


def concrete_detail(category: Category, subsystem: str, rng: random.Random) -> str:
    """One specific situation for an ask, phrased as a note to the generator."""
    if category is Category.PARTS_REQUEST:
        return f"the part needed is {rng.choice(PARTS[subsystem])}"
    if category is Category.WARRANTY_CLAIM:
        return f"the failed component was {rng.choice(PARTS[subsystem])}, already replaced"
    if category is Category.TECHNICAL_FAULT:
        return f"the symptom is {rng.choice(SYMPTOMS[subsystem])}"
    if category is Category.SERVICE_SCHEDULING:
        return f"the dealer {rng.choice(SCHEDULING)}"
    if category is Category.BILLING:
        return f"the problem: {rng.choice(BILLING)}"
    return f"the dealer {rng.choice(OTHER)}"
