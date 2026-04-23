"""Unit tests for pdf_smasher.utils.text."""

from __future__ import annotations

import pytest

from pdf_smasher.utils.text import format_page_list_short


def test_short_list_unchanged() -> None:
    assert format_page_list_short([1, 2, 3]) == "[1, 2, 3]"


def test_sorts_output() -> None:
    assert format_page_list_short([5, 1, 3]) == "[1, 3, 5]"


def test_at_limit_not_truncated() -> None:
    got = format_page_list_short(list(range(1, 11)), limit=10)
    assert got == "[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]"


def test_over_limit_truncates() -> None:
    got = format_page_list_short(list(range(1, 12)), limit=10)
    assert got == "[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, ... (+1 more)]"


def test_huge_list_stays_short() -> None:
    """Regression: DCR Wave 2 flagged 1M-page out_of_range lists as
    multi-MB stderr spew. Output must stay compact regardless of input
    size."""
    ids = list(range(1, 1_000_001))
    got = format_page_list_short(ids, limit=10)
    assert len(got) < 100, f"expected short output; got {len(got)} chars: {got}"
    assert got.endswith("(+999990 more)]")


def test_invalid_limit() -> None:
    with pytest.raises(ValueError, match=r"limit"):
        format_page_list_short([1, 2, 3], limit=0)
