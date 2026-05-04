"""Each bomb must refuse with a structured exit code."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

CORPUS = Path("tests/corpus/bombs")


@pytest.fixture(scope="session", autouse=True)
def _ensure_corpus() -> None:
    expected = ["huge_page_dimensions.pdf", "xref_loop.pdf", "objstm_explosion.pdf"]
    missing = [n for n in expected if not (CORPUS / n).exists()]
    if missing:
        pytest.skip(f"bomb corpus missing: {missing} — run scripts/generate_bomb_corpus.py")


def _run(input_pdf: Path, tmp_path: Path) -> tuple[int, float, str]:
    env = os.environ.copy()
    env["HANKPDF_SKIP_ENV_CHECK"] = "1"
    out_pdf = tmp_path / "out.pdf"
    t0 = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-m", "hankpdf.cli.main", str(input_pdf), "-o", str(out_pdf)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    elapsed = time.monotonic() - t0
    return proc.returncode, elapsed, proc.stderr


@pytest.mark.parametrize(
    "fixture, expected_code",
    [
        ("huge_page_dimensions.pdf", 16),  # DECOMPRESSION_BOMB
        ("xref_loop.pdf", 13),  # CORRUPT
        ("objstm_explosion.pdf", 12),  # OVERSIZE (max_pages)
    ],
)
def test_bomb_refused_quickly(tmp_path: Path, fixture: str, expected_code: int) -> None:
    rc, elapsed, stderr = _run(CORPUS / fixture, tmp_path)
    assert rc == expected_code, (
        f"unexpected rc={rc} for {fixture} (expected {expected_code}); "
        f"if this is EXIT_ENGINE_ERROR=30, the structured refusal isn't wired. "
        f"stderr=\n{stderr}"
    )
    assert elapsed < 30, f"bomb {fixture} took {elapsed:.1f}s — escaped the gates"
