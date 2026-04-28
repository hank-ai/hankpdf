"""CLI-layer regression: every stderr warning/error line uses a stable code.

Reviewer C flagged stderr warnings as free prose (hard to grep in batch logs).
The :mod:`pdf_smasher.cli.warning_codes` module defines stable codes like
``W-CHUNKS-EXCEED-CAP`` and :func:`emit` wraps every ``[hankpdf] warning``
line. These tests drive each emission path and assert the code appears.
"""

from __future__ import annotations

import io

import pikepdf
import pytest

from pdf_smasher.cli.main import main
from pdf_smasher.cli.warning_codes import CliErrorCode, CliWarningCode


def _make_pdf(tmp_path, n_pages: int = 2):  # type: ignore[no-untyped-def]
    """Minimal N-page PDF. Each page 612x792 (US Letter), image XObject
    attached so the per-page MRC gate doesn't whole-doc-passthrough."""
    pdf = pikepdf.new()
    for _ in range(n_pages):
        page = pdf.add_blank_page(page_size=(612, 792))
        img = pdf.make_stream(
            b"\x00" * 2048,
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Image,
            Width=10,
            Height=10,
            BitsPerComponent=8,
            ColorSpace=pikepdf.Name.DeviceRGB,
            Filter=pikepdf.Name.FlateDecode,
        )
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=img))
        page.Contents = pdf.make_stream(b"q 612 0 0 792 0 0 cm /Im0 Do Q\n")
    p = tmp_path / "in.pdf"
    pdf.save(p)
    return p


def _assert_code(code: CliWarningCode, text: str) -> None:
    assert f"[{code}]" in text, f"expected stable code [{code}] in stderr; got:\n{text}"


def _assert_error_code(code: CliErrorCode, text: str) -> None:
    assert f"[{code}]" in text, f"expected stable error code [{code}] in stderr; got:\n{text}"


@pytest.mark.integration
def test_w_max_output_mb_image_mode(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    """--max-output-mb + image format → W-MAX-OUTPUT-MB-IMAGE-MODE."""
    in_path = _make_pdf(tmp_path, n_pages=1)
    out_path = tmp_path / "out.jpg"
    rc = main(
        [
            str(in_path),
            "-o",
            str(out_path),
            "--max-output-mb",
            "1",
        ]
    )
    assert rc == 0, f"rc={rc}"
    err = capsys.readouterr().err
    _assert_code("W-MAX-OUTPUT-MB-IMAGE-MODE", err)


@pytest.mark.integration
def test_w_output_format_extension_override(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    """--output-format contradicts -o extension → W-OUTPUT-FORMAT-EXTENSION-OVERRIDE."""
    in_path = _make_pdf(tmp_path, n_pages=1)
    out_path = tmp_path / "out.pdf"
    rc = main(
        [
            str(in_path),
            "-o",
            str(out_path),
            "--output-format",
            "jpeg",
        ]
    )
    assert rc == 0, f"rc={rc}"
    err = capsys.readouterr().err
    _assert_code("W-OUTPUT-FORMAT-EXTENSION-OVERRIDE", err)


@pytest.mark.integration
def test_w_chunks_exceed_cap(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    """Oversize single-page chunk in multi-chunk mode → W-CHUNKS-EXCEED-CAP."""
    # 3 pages, tiny 0.005 MB cap — every page ends up its own chunk, and
    # most (or all) exceed the cap since realistic pages are ~5-15 KB.
    in_path = _make_pdf(tmp_path, n_pages=3)
    out_path = tmp_path / "smol.pdf"
    rc = main(
        [
            str(in_path),
            "-o",
            str(out_path),
            "--max-output-mb",
            "0.005",
            "--accept-drift",
            "--min-ratio",
            "0",
        ]
    )
    assert rc == 0, f"rc={rc}"
    err = capsys.readouterr().err
    # This combo reliably fires both the per-chunk exceed warning (multi-chunk)
    # or the single-chunk oversize warning. Accept either.
    assert "[W-CHUNKS-EXCEED-CAP]" in err or "[W-SINGLE-CHUNK-OVERSIZE]" in err, (
        f"expected a chunk-cap code; got:\n{err}"
    )


@pytest.mark.integration
def test_w_stale_chunk_files(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    """Pre-existing high-index chunks from a prior run → W-STALE-CHUNK-FILES."""
    in_path = _make_pdf(tmp_path, n_pages=2)
    out_path = tmp_path / "smol.pdf"
    # Seed a stale file with a high index (> any new chunk count).
    stale = tmp_path / "smol_099.pdf"
    stale.write_bytes(b"%PDF-1.7\n%stale\n")
    rc = main(
        [
            str(in_path),
            "-o",
            str(out_path),
            "--max-output-mb",
            "0.005",
            "--accept-drift",
            "--min-ratio",
            "0",
        ]
    )
    assert rc == 0, f"rc={rc}"
    err = capsys.readouterr().err
    _assert_code("W-STALE-CHUNK-FILES", err)


@pytest.mark.integration
def test_w_verifier_skipped(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    """Default skip_verify → W-VERIFIER-SKIPPED banner on stderr."""
    in_path = _make_pdf(tmp_path, n_pages=1)
    out_path = tmp_path / "out.pdf"
    rc = main([str(in_path), "-o", str(out_path)])
    assert rc == 0, f"rc={rc}"
    err = capsys.readouterr().err
    _assert_code("W-VERIFIER-SKIPPED", err)


@pytest.mark.integration
def test_w_max_output_mb_stdout(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """--max-output-mb + -o - → W-MAX-OUTPUT-MB-STDOUT (only when output > cap)."""
    in_path = _make_pdf(tmp_path, n_pages=2)

    # Redirect stdout so the PDF bytes don't contaminate pytest's capture.
    fake_stdout = io.BytesIO()

    class _FakeStdout:
        buffer = fake_stdout

    import io as _io
    import sys as _sys

    real_stdout = _sys.stdout
    real_stderr = _sys.stderr
    fake_stderr = _io.StringIO()
    monkeypatch.setattr(_sys, "stdout", _FakeStdout())  # type: ignore[arg-type]
    monkeypatch.setattr(_sys, "stderr", fake_stderr)
    try:
        rc = main(
            [
                str(in_path),
                "-o",
                "-",
                "--max-output-mb",
                "0.000001",  # ensures output exceeds cap
            ]
        )
    finally:
        _sys.stdout = real_stdout
        _sys.stderr = real_stderr
    assert rc == 0, f"rc={rc}"
    err = fake_stderr.getvalue()
    _assert_code("W-MAX-OUTPUT-MB-STDOUT", err)


# ---------- refusal error codes (E-*) ----------


@pytest.mark.integration
def test_e_input_encrypted_code(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    """Encrypted PDF without password → E-INPUT-ENCRYPTED."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    in_path = tmp_path / "enc.pdf"
    pdf.save(in_path, encryption=pikepdf.Encryption(user="s", owner="o"))
    out_path = tmp_path / "out.pdf"
    rc = main([str(in_path), "-o", str(out_path)])
    assert rc == 10, f"expected EXIT_ENCRYPTED=10, got {rc}"
    err = capsys.readouterr().err
    _assert_error_code("E-INPUT-ENCRYPTED", err)


@pytest.mark.integration
def test_e_input_signed_code(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    """Signed PDF without opt-in → E-INPUT-SIGNED."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.Root["/AcroForm"] = pikepdf.Dictionary(SigFlags=3, Fields=pikepdf.Array([]))
    in_path = tmp_path / "signed.pdf"
    pdf.save(in_path)
    out_path = tmp_path / "out.pdf"
    rc = main([str(in_path), "-o", str(out_path)])
    assert rc == 11, f"expected EXIT_SIGNED=11, got {rc}"
    err = capsys.readouterr().err
    _assert_error_code("E-INPUT-SIGNED", err)


@pytest.mark.integration
def test_e_input_oversize_code(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    """Input > max-input-mb → E-INPUT-OVERSIZE."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    in_path = tmp_path / "in.pdf"
    pdf.save(in_path)
    out_path = tmp_path / "out.pdf"
    # Set a tiny max to guarantee a refusal.
    rc = main(
        [
            str(in_path),
            "-o",
            str(out_path),
            "--max-input-mb",
            "0.0001",
        ]
    )
    assert rc == 12, f"expected EXIT_OVERSIZE=12, got {rc}"
    err = capsys.readouterr().err
    _assert_error_code("E-INPUT-OVERSIZE", err)
