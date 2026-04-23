"""Verify log-redaction helpers match docs/SPEC.md §9.2."""

from __future__ import annotations

from pdf_smasher.utils.log import redact_filename, redact_text

HASH_PREFIX_LEN = 8  # per docs/SPEC.md §9.2 — sha1(basename)[:8]


def test_redact_filename_stable_format() -> None:
    result = redact_filename("patient-records-2026.pdf")
    prefix, _, tail = result.partition("…")
    assert len(prefix) == HASH_PREFIX_LEN
    assert tail == "2026.pdf"


def test_redact_filename_short_name() -> None:
    result = redact_filename("a.pdf")
    prefix, _, tail = result.partition("…")
    assert len(prefix) == HASH_PREFIX_LEN
    assert tail == "a.pdf"


def test_redact_filename_strips_path() -> None:
    a = redact_filename("/tmp/secret/patient-records-2026.pdf")
    b = redact_filename("patient-records-2026.pdf")
    assert a == b, "redaction must ignore directory components"


def test_redact_text_never_returns_content() -> None:
    secret = "SSN 123-45-6789 diagnosis: something"
    assert secret not in redact_text(secret)
    assert redact_text(secret).startswith("<redacted:")
