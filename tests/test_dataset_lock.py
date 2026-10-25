"""The test split is frozen: its SHA-256 must match TEST_SET_LOCK. Only the bytes are hashed;
the contents are never read here (CLAUDE.md: tune on dev only)."""

import hashlib

import pytest

from fielddesk.config import get_settings

DATA = get_settings().data_dir
LOCK = DATA / "TEST_SET_LOCK"
TEST_FILE = DATA / "tickets_test.jsonl"


@pytest.mark.skipif(not LOCK.exists(), reason="dataset not built yet (make validate-data)")
def test_test_split_matches_lock() -> None:
    expected, name = LOCK.read_text(encoding="utf-8").split()
    assert name == TEST_FILE.name
    assert TEST_FILE.exists(), "TEST_SET_LOCK exists but tickets_test.jsonl is missing"
    actual = hashlib.sha256(TEST_FILE.read_bytes()).hexdigest()
    assert actual == expected, "tickets_test.jsonl changed after it was locked"
