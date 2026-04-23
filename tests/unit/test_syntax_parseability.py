"""Guard against re-landing Python-2-style `except A, B:` syntax.

Wave 2 CRIT-2 caught both triage.py and cli/main.py in this state with
121 tests green because neither affected codepath is exercised by the
unit suite. This test walks the whole package via ast.parse so a
SyntaxError anywhere blocks the merge.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PKG_ROOT = Path(__file__).resolve().parents[2] / "pdf_smasher"


@pytest.mark.parametrize("path", sorted(_PKG_ROOT.rglob("*.py")))
def test_every_py_file_parses(path: Path) -> None:
    ast.parse(path.read_text(encoding="utf-8"))
