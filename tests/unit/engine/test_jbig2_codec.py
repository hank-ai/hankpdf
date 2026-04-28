"""Tests for hankpdf.engine.codecs.jbig2 — 1-bit image -> JBIG2 bytes.

Uses the system ``jbig2`` binary (jbig2enc) via subprocess. Generic region
coding only — no symbol mode, no refinement (`-r`) — per the safety
decision in SPEC.md §4.3.3 (avoids both the Xerox 6/8 substitution risk
and the documented Acrobat crash on refinement).
"""

from __future__ import annotations

import subprocess

import numpy as np
import pytest
from PIL import Image

from hankpdf.engine.codecs.jbig2 import encode_1bit_jbig2


def _jbig2_available() -> bool:
    try:
        subprocess.run(["jbig2", "--version"], capture_output=True, check=False, timeout=2)
    except FileNotFoundError, subprocess.TimeoutExpired:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _jbig2_available(),
    reason="system jbig2enc not installed (brew install jbig2enc)",
)


def _tiny_mask() -> Image.Image:
    arr = np.zeros((64, 64), dtype=bool)
    arr[10:30, 10:30] = True
    return Image.fromarray(arr).convert("1")


def _text_shape_mask() -> Image.Image:
    """Denser mask simulating text glyphs."""
    rng = np.random.default_rng(0)
    arr = np.zeros((400, 400), dtype=bool)
    for _ in range(60):
        y0 = int(rng.integers(10, 380))
        x0 = int(rng.integers(10, 370))
        arr[y0 : y0 + 8, x0 : x0 + 20] = True
    return Image.fromarray(arr).convert("1")


def test_encode_returns_bytes() -> None:
    mask = _tiny_mask()
    out = encode_1bit_jbig2(mask)
    assert isinstance(out, bytes)
    assert len(out) > 0


def test_encode_smaller_than_raw_1bit_for_repetitive_pattern() -> None:
    """For a simple filled rectangle, JBIG2 should comfortably beat raw 1-bit."""
    mask = _tiny_mask()
    out = encode_1bit_jbig2(mask)
    raw_size = mask.size[0] * mask.size[1] // 8  # raw 1-bit-per-pixel size
    assert len(out) < raw_size


def test_encode_handles_larger_mask() -> None:
    mask = _text_shape_mask()
    out = encode_1bit_jbig2(mask)
    assert len(out) > 0
    # Bigger mask should still beat raw packed-1-bit by a healthy margin.
    raw = mask.size[0] * mask.size[1] // 8
    assert len(out) < raw


def test_encode_roundtrip_embeds_in_pdf_and_pdfium_decodes() -> None:
    """Embed the JBIG2 bytes in a PDF and verify pdfium can render it."""
    import io

    import pikepdf
    import pypdfium2 as pdfium

    mask = _tiny_mask()
    jbig2_bytes = encode_1bit_jbig2(mask)

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(64, 64))
    page = pdf.pages[0]
    xobj = pdf.make_stream(
        jbig2_bytes,
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=mask.size[0],
        Height=mask.size[1],
        ColorSpace=pikepdf.Name.DeviceGray,
        BitsPerComponent=1,
        Filter=pikepdf.Name.JBIG2Decode,
    )
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im=xobj))
    page.Contents = pdf.make_stream(b"q 64 0 0 64 0 0 cm /Im Do Q\n")
    buf = io.BytesIO()
    pdf.save(buf)

    # pdfium must be able to decode this — prove interop works.
    doc = pdfium.PdfDocument(buf.getvalue())
    try:
        rendered = doc[0].render(scale=1.0).to_pil().convert("L")
    finally:
        doc.close()
    assert rendered.size == (64, 64)


def test_encode_raises_on_non_1bit_image() -> None:
    """Caller must pass a '1' mode image. RGB would silently produce wrong output."""
    rgb = Image.new("RGB", (64, 64), color="white")
    with pytest.raises(ValueError, match="1-bit"):
        encode_1bit_jbig2(rgb)
