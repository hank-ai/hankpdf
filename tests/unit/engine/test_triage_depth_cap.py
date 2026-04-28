"""Triage refuses to silently waive nested resource trees past the depth cap."""

from __future__ import annotations

import pytest

from hankpdf.engine.triage import _walk_dict_for_names
from hankpdf.exceptions import MaliciousPDFError


def test_walk_dict_at_cap_boundary_passes() -> None:
    # Calling with depth==max_depth must NOT raise (boundary is `>`, not `>=`).
    result = _walk_dict_for_names({}, frozenset({"JS"}), set(), depth=64, max_depth=64)
    assert result == set()


def test_walk_dict_one_past_cap_raises_malicious() -> None:
    # depth=65 with max_depth=64 must raise.
    with pytest.raises(MaliciousPDFError):
        _walk_dict_for_names({}, frozenset({"JS"}), set(), depth=65, max_depth=64)
