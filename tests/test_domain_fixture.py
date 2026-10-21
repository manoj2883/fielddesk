import re
from collections import Counter

from data.generator.domain import load_domain

REAL_BRANDS = re.compile(
    r"(?i)\b(john deere|deere|case ih|new holland|kubota|massey|fendt|claas|agco|caterpillar|"
    r"bobcat|jcb|komatsu|valtra|mahindra)\b"
)


def test_sizes_match_spec() -> None:
    d = load_domain()
    assert len(d.machines) == 8
    assert len(d.codes) == 25
    assert len(d.dealers) == 15


def test_every_machine_type_is_covered() -> None:
    assert {m.type for m in load_domain().machines} == {"tractor", "combine", "loader"}


def test_codes_are_unique_and_well_formed() -> None:
    codes = [c.code for c in load_domain().codes]
    assert len(codes) == len(set(codes))
    assert all(re.fullmatch(r"RX-E\d{3}", c) for c in codes)


def test_safety_codes_cover_the_named_hazards() -> None:
    safety = [c for c in load_domain().codes if c.safety]
    assert 5 <= len(safety) <= 8
    assert {"brakes", "hydraulics", "steering"} <= {c.subsystem for c in safety}
    assert any("fire" in c.meaning for c in safety)


def test_every_machine_type_has_safe_and_safety_codes() -> None:
    d = load_domain()
    for machine_type in ("tractor", "combine", "loader"):
        assert d.codes_for(machine_type, safety=True)
        assert d.codes_for(machine_type, safety=False)


def test_dealer_voices_are_varied() -> None:
    voices = Counter(d.voice for d in load_domain().dealers)
    assert len(voices) >= 5
    assert max(voices.values()) <= 4


def test_no_real_brand_names() -> None:
    d = load_domain()
    text = " ".join(
        [m.model + m.description for m in d.machines]
        + [dl.name + dl.notes for dl in d.dealers]
        + [c.meaning for c in d.codes]
    )
    assert not REAL_BRANDS.search(text)
