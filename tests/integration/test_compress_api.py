"""Tests for the full ``compress()`` public API.

This is the end-to-end integration: triage → sanitize → recompress →
verify → report. Proves that ``from pdf_smasher import compress`` works
as the SPEC.md §1 contract promises.
"""

from __future__ import annotations

import io

import pikepdf
import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageDraw, ImageFont

from pdf_smasher import (
    CompressOptions,
    CompressReport,
    EncryptedPDFError,
    SignedPDFError,
    compress,
)


def _make_fake_scan(text_lines: list[str], *, source_dpi: int = 200) -> bytes:
    w_pt, h_pt = 612.0, 792.0
    w_px = round(w_pt * source_dpi / 72)
    h_px = round(h_pt * source_dpi / 72)
    img = Image.new("RGB", (w_px, h_px), color="white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("Arial.ttf", 60)
    except OSError:
        font = ImageFont.load_default(size=60)
    y = 200
    for line in text_lines:
        draw.text((150, y), line, fill="black", font=font)
        y += 120

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(w_pt, h_pt))
    page = pdf.pages[0]
    jbuf = io.BytesIO()
    img.save(jbuf, format="JPEG", quality=92, subsampling=0)
    xobj = pdf.make_stream(
        jbuf.getvalue(),
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=w_px,
        Height=h_px,
        ColorSpace=pikepdf.Name.DeviceRGB,
        BitsPerComponent=8,
        Filter=pikepdf.Name.DCTDecode,
    )
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Scan=xobj))
    page.Contents = pdf.make_stream(b"q 612 0 0 792 0 0 cm /Scan Do Q\n")
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


@pytest.mark.integration
def test_compress_returns_bytes_and_report() -> None:
    pdf_in = _make_fake_scan(["HELLO WORLD"])
    pdf_out, report = compress(pdf_in)
    assert isinstance(pdf_out, bytes)
    assert isinstance(report, CompressReport)
    assert pdf_out.startswith(b"%PDF-")


@pytest.mark.integration
def test_compress_report_fields_populated() -> None:
    pdf_in = _make_fake_scan(["Invoice 12345 total $50.00"])
    pdf_out, report = compress(pdf_in)
    assert report.input_bytes == len(pdf_in)
    assert report.output_bytes == len(pdf_out)
    assert report.ratio > 0
    assert report.pages == 1
    assert report.wall_time_ms >= 0
    assert len(report.input_sha256) == 64
    assert len(report.output_sha256) == 64
    assert report.canonical_input_sha256 is None or len(report.canonical_input_sha256) == 64


@pytest.mark.integration
def test_compress_preserves_text_searchability() -> None:
    pdf_in = _make_fake_scan(["SEARCHABLE"])
    # ocr=True: embedding a searchable text layer is opt-in (defaults off).
    pdf_out, _ = compress(pdf_in, options=CompressOptions(ocr=True))
    pdf = pdfium.PdfDocument(pdf_out)
    try:
        tp = pdf[0].get_textpage()
        text = tp.get_text_bounded()
        tp.close()
    finally:
        pdf.close()
    assert "SEARCHABLE" in text


@pytest.mark.integration
def test_compress_encrypted_without_password_raises() -> None:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    buf = io.BytesIO()
    pdf.save(buf, encryption=pikepdf.Encryption(user="s", owner="o"))
    with pytest.raises(EncryptedPDFError):
        compress(buf.getvalue())


@pytest.mark.integration
def test_compress_signed_without_opt_in_raises() -> None:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.Root["/AcroForm"] = pikepdf.Dictionary(SigFlags=3, Fields=pikepdf.Array([]))
    buf = io.BytesIO()
    pdf.save(buf)
    with pytest.raises(SignedPDFError):
        compress(buf.getvalue())


@pytest.mark.integration
def test_compress_respects_mode_safe() -> None:
    """Safe mode runs but enforces stricter gates. On valid content, still passes.

    skip_verify defaults to True, so verifier status will be 'skipped' unless
    explicitly enabled. This test verifies safe mode completes without error.
    """
    pdf_in = _make_fake_scan(["HELLO WORLD"])
    pdf_out, report = compress(pdf_in, options=CompressOptions(mode="safe"))
    assert pdf_out.startswith(b"%PDF-")
    assert report.verifier.status in ("pass", "fail", "skipped")


def test_legal_codec_profile_raises_not_implemented() -> None:
    """legal_codec_profile names a CCITT G4 profile we have NOT implemented.
    Guard must fire before triage so the error is clear even on b\"\"."""
    with pytest.raises(NotImplementedError, match="legal_codec_profile"):
        compress(b"", options=CompressOptions(legal_codec_profile="ccitt-g4"))


def test_legal_codec_profile_none_does_not_raise_at_construction() -> None:
    """Default value (None) must not fire the guard — the previous bool=False
    typing was wrong (False is not None is True, raising every call)."""
    opts = CompressOptions()
    assert opts.legal_codec_profile is None


def test_compress_skip_verify_reports_status_skipped() -> None:
    """With skip_verify=True (the default), the returned CompressReport
    must surface status='skipped' rather than a fake 'pass', and append
    a 'verifier-skipped' warning."""
    pdf_in = _make_fake_scan(["HELLO"])
    _, report = compress(pdf_in, options=CompressOptions(skip_verify=True))
    assert report.verifier.status == "skipped", (
        f"expected skipped, got {report.verifier.status}"
    )
    assert any(w == "verifier-skipped" for w in report.warnings), (
        f"expected 'verifier-skipped' in warnings; got {report.warnings}"
    )


def test_compress_verify_true_reports_real_status() -> None:
    """With skip_verify=False, the verifier runs and status is 'pass' or
    'fail' (not 'skipped'). No 'verifier-skipped' warning."""
    pdf_in = _make_fake_scan(["HELLO"])
    _, report = compress(pdf_in, options=CompressOptions(skip_verify=False))
    assert report.verifier.status in ("pass", "fail"), (
        f"expected pass|fail, got {report.verifier.status}"
    )
    assert not any(w == "verifier-skipped" for w in report.warnings)
