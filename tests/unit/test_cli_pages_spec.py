"""Pure unit tests for the CLI --pages spec parser."""

from __future__ import annotations

import pytest

from pdf_smasher.cli.main import _parse_pages_spec


def test_single_page() -> None:
    assert _parse_pages_spec("5") == {5}


def test_range() -> None:
    assert _parse_pages_spec("3-5") == {3, 4, 5}


def test_comma_list() -> None:
    assert _parse_pages_spec("1,3,5") == {1, 3, 5}


def test_mixed_comma_range() -> None:
    assert _parse_pages_spec("1,3-5,10") == {1, 3, 4, 5, 10}


def test_single_range_is_same_as_page() -> None:
    assert _parse_pages_spec("7-7") == {7}


def test_backward_range_rejected() -> None:
    with pytest.raises(ValueError, match="range"):
        _parse_pages_spec("5-3")


def test_zero_rejected() -> None:
    with pytest.raises(ValueError, match="1-indexed"):
        _parse_pages_spec("0")


def test_empty_returns_empty_set() -> None:
    # The CLI layer treats this as an error (exits 40) but the parser
    # itself returns an empty set — the guard is at call-sites.
    assert _parse_pages_spec("") == set()
    assert _parse_pages_spec(",,,") == set()


def test_extremely_large_range_rejected() -> None:
    """Regression gate: DCR Wave 1 flagged --pages '1-99999999999' as
    a memory-exhaustion DoS. Cap range sizes to 1,000,000."""
    with pytest.raises(ValueError, match=r"too large|cap"):
        _parse_pages_spec("1-99999999999")


def test_range_at_cap_accepted() -> None:
    # 1_000_000 pages is the limit; cap+1 is rejected.
    result = _parse_pages_spec("1-1000000")
    assert len(result) == 1_000_000


def test_range_over_cap_rejected() -> None:
    with pytest.raises(ValueError, match=r"too large|cap"):
        _parse_pages_spec("1-1000001")


def test_multi_range_total_cardinality_capped() -> None:
    """Regression: 1M cap is on TOTAL set, not per-range."""
    spec = ",".join(f"{1 + i * 2_000_000}-{1_000_000 + i * 2_000_000}" for i in range(10))
    with pytest.raises(ValueError, match=r"too large|cap|total"):
        _parse_pages_spec(spec)


@pytest.mark.parametrize("spec", ["-5", "abc", "1-2-3", "1,abc", "1-", "-", "1-abc"])
def test_helpful_error_message_mentions_pages_context(spec: str) -> None:
    """Malformed --pages input must raise ValueError whose message
    mentions '--pages' / 'pages' context, not raw int() noise.

    Reviewer B: ``_parse_pages_spec("-5")`` used to raise
    ``invalid literal for int() with base 10: ''`` with no --pages
    reference. Wrap every int() conversion with context so scripts
    parsing stderr can map failures back to the flag.
    """
    with pytest.raises(ValueError, match=r"(?i)--pages|pages"):
        _parse_pages_spec(spec)
