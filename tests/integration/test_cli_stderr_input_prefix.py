"""Regression: every CLI stderr summary line carries the redacted input name.

Reviewer C: batch scripts tee stderr from multiple concurrent jobs to a
single log file. Without the input-filename prefix, a summary line like
"[hankpdf] wrote 12 chunks …" had no reference to *which* input it belonged
to. Now every stderr line is prefixed with the redacted input name per
THREAT_MODEL.md §5 (sha1-prefix + tail).
"""

from __future__ import annotations

import pikepdf
import pytest

from pdf_smasher.cli.main import main
from pdf_smasher.utils.log import redact_filename


def _make_pdf(tmp_path, n_pages: int = 2, name: str = "weird-input-2026-04.pdf"):  # type: ignore[no-untyped-def]
    pdf = pikepdf.new()
    for _ in range(n_pages):
        pdf.add_blank_page(page_size=(612, 792))
    p = tmp_path / name
    pdf.save(p)
    return p


@pytest.mark.integration
def test_stderr_warning_lines_include_redacted_input_name(
    tmp_path,  # type: ignore[no-untyped-def]
    capsys,  # type: ignore[no-untyped-def]
) -> None:
    """A CLI run that emits the verifier-skipped banner must render that
    line with the redacted input filename so batch logs are disambiguated.
    """
    in_path = _make_pdf(tmp_path)
    out_path = tmp_path / "out.pdf"
    rc = main([str(in_path), "-o", str(out_path)])
    assert rc == 0, f"rc={rc}"
    err = capsys.readouterr().err
    tag = redact_filename(in_path)
    assert tag in err, f"expected redacted filename {tag!r} in stderr; got:\n{err}"
    # Must appear specifically on the W-VERIFIER-SKIPPED warning line.
    lines = [ln for ln in err.splitlines() if "[W-VERIFIER-SKIPPED]" in ln]
    assert lines, "no verifier-skipped line in stderr"
    assert all(tag in ln for ln in lines), (
        f"verifier-skipped line missing redacted input name; got: {lines}"
    )


@pytest.mark.integration
def test_stderr_summary_lines_include_redacted_input_name(
    tmp_path,  # type: ignore[no-untyped-def]
    capsys,  # type: ignore[no-untyped-def]
) -> None:
    """Non-warning summary lines (triage, merge, etc.) must also carry
    the redacted filename prefix."""
    in_path = _make_pdf(tmp_path)
    out_path = tmp_path / "out.pdf"
    rc = main([str(in_path), "-o", str(out_path)])
    assert rc == 0, f"rc={rc}"
    err = capsys.readouterr().err
    tag = redact_filename(in_path)
    # Triage line is the first progress-callback line after the banner.
    triage_lines = [ln for ln in err.splitlines() if "triage" in ln and "[hankpdf]" in ln]
    assert triage_lines, f"no triage line found; stderr:\n{err}"
    assert all(tag in ln for ln in triage_lines), (
        f"triage line missing redacted input name; got: {triage_lines}"
    )


@pytest.mark.integration
def test_stderr_stdin_input_has_plain_prefix(
    tmp_path,  # type: ignore[no-untyped-def]
    monkeypatch,  # type: ignore[no-untyped-def]
    capsys,  # type: ignore[no-untyped-def]
) -> None:
    """stdin input (-) has no filename to redact; the plain '[hankpdf] '
    prefix is acceptable — no empty hash."""
    in_path = _make_pdf(tmp_path)
    out_path = tmp_path / "out.pdf"

    class _StdinBytes:
        @property
        def buffer(self):  # type: ignore[no-untyped-def]
            return _inner

    _inner = type("_I", (), {"read": lambda self: in_path.read_bytes()})()

    import sys as _sys

    monkeypatch.setattr(_sys, "stdin", _StdinBytes())  # type: ignore[arg-type]

    rc = main(["-", "-o", str(out_path)])
    assert rc == 0, f"rc={rc}"
    err = capsys.readouterr().err
    # No empty hash pattern like "[hankpdf] …:" with an empty middle.
    assert "[hankpdf] :" not in err, f"stdin input produced empty hash; stderr:\n{err}"
