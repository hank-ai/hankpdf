"""End-to-end behavior of the per-page MRC gate."""

from __future__ import annotations

import io

import pikepdf
import pytest

from pdf_smasher import compress
from pdf_smasher.types import CompressOptions


def _make_text_only_pdf() -> bytes:
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(
            F1=pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.Type1,
                BaseFont=pikepdf.Name.Helvetica,
                Encoding=pikepdf.Name.WinAnsiEncoding,
            ),
        )
    )
    page.Contents = pdf.make_stream(b"BT /F1 24 Tf 100 700 Td (Hello World) Tj ET\n")
    buf = io.BytesIO()
    pdf.save(buf, linearize=False)
    return buf.getvalue()


def _make_image_only_pdf() -> bytes:
    """One-page PDF with a 50KB image XObject."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    image_stream = pdf.make_stream(
        b"\x00" * 50_000,
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=100,
        Height=100,
        BitsPerComponent=8,
        ColorSpace=pikepdf.Name.DeviceRGB,
        Filter=pikepdf.Name.FlateDecode,
    )
    page.Resources = pikepdf.Dictionary(
        XObject=pikepdf.Dictionary(Im0=image_stream),
    )
    page.Contents = pdf.make_stream(b"q 612 0 0 792 0 0 cm /Im0 Do Q\n")
    buf = io.BytesIO()
    pdf.save(buf, linearize=False)
    return buf.getvalue()


def _make_2page_mixed_pdf() -> bytes:
    """Page 0: text-only. Page 1: image-dominated. Tests partial MRC."""
    text = _make_text_only_pdf()
    image = _make_image_only_pdf()
    text_pdf = pikepdf.open(io.BytesIO(text))
    image_pdf = pikepdf.open(io.BytesIO(image))
    text_pdf.pages.append(image_pdf.pages[0])
    buf = io.BytesIO()
    text_pdf.save(buf, linearize=False)
    image_pdf.close()
    text_pdf.close()
    return buf.getvalue()


def test_native_pdf_hits_whole_doc_passthrough() -> None:
    """100% native PDF: whole-doc shortcut fires; output is byte-identical."""
    pdf_bytes = _make_text_only_pdf()
    out_bytes, report = compress(pdf_bytes, options=CompressOptions())
    assert out_bytes == pdf_bytes
    assert report.status == "passed_through"
    assert any("passthrough-no-image-content" in w for w in report.warnings)


def test_re_ocr_disables_the_gate() -> None:
    """--re-ocr forces every page through MRC even on a native PDF."""
    pdf_bytes = _make_text_only_pdf()
    options = CompressOptions(re_ocr=True, skip_verify=True)
    _, report = compress(pdf_bytes, options=options)
    assert not any("passthrough-no-image-content" in w for w in report.warnings)


def test_strip_text_layer_disables_the_gate() -> None:
    """--strip-text-layer forces every page through MRC."""
    pdf_bytes = _make_text_only_pdf()
    options = CompressOptions(strip_text_layer=True, skip_verify=True)
    _, report = compress(pdf_bytes, options=options)
    assert not any("passthrough-no-image-content" in w for w in report.warnings)


def test_legal_mode_disables_the_gate() -> None:
    """--legal-mode (CCITT G4 archival) forces every page through MRC.

    The user invoked legal_codec_profile to guarantee a re-encode under the
    archival profile. Verbatim copy would defeat the guarantee — the gate
    must back off when this option is non-None.
    """
    pdf_bytes = _make_text_only_pdf()
    options = CompressOptions(legal_codec_profile="ccitt-g4", skip_verify=True, accept_drift=True)
    # legal_codec_profile is currently RESERVED; we expect a
    # NotImplementedError (per types.py docstring) which is fine — the
    # important assertion is that the gate did NOT short-circuit before
    # the engine got the chance to refuse.
    with pytest.raises(NotImplementedError):
        compress(pdf_bytes, options=options)


def test_threshold_zero_forces_full_pipeline() -> None:
    """min_image_byte_fraction=0.0 disables the gate (every page MRC-worthy)."""
    pdf_bytes = _make_text_only_pdf()
    options = CompressOptions(min_image_byte_fraction=0.0, skip_verify=True)
    _, report = compress(pdf_bytes, options=options)
    assert not any("passthrough-no-image-content" in w for w in report.warnings)


@pytest.mark.slow
def test_verify_disables_the_gate() -> None:
    """--verify forces every page through MRC + verifier so the
    aggregator's metrics aren't polluted by synthetic verdicts."""
    pdf_bytes = _make_text_only_pdf()
    options = CompressOptions(skip_verify=False)
    _, report = compress(pdf_bytes, options=options)
    assert not any("passthrough-no-image-content" in w for w in report.warnings)


def test_partial_mrc_run_skips_only_text_pages() -> None:
    """2-page mixed PDF: page 0 (text) is verbatim; page 1 (image) is MRC'd.

    Asserts the report carries the right pages_skipped_verbatim indices
    AND the aggregate warning code fires.
    """
    pdf_bytes = _make_2page_mixed_pdf()
    _, report = compress(pdf_bytes, options=CompressOptions())
    assert report.status != "passed_through"
    assert report.pages_skipped_verbatim == (0,)
    assert any(w.startswith("pages-skipped-verbatim-") for w in report.warnings)


def _make_4page_alternating_pdf() -> bytes:
    """4 pages: text, image, text, image. Tests that pages_skipped_verbatim
    indices survive non-contiguous skip patterns and arrive sorted."""
    text = _make_text_only_pdf()
    image = _make_image_only_pdf()
    out = pikepdf.open(io.BytesIO(text))
    image_pdf = pikepdf.open(io.BytesIO(image))
    # text already has page 0; append image, text, image to reach indices 1,2,3.
    out.pages.append(image_pdf.pages[0])
    text2 = pikepdf.open(io.BytesIO(_make_text_only_pdf()))
    out.pages.append(text2.pages[0])
    image2 = pikepdf.open(io.BytesIO(_make_image_only_pdf()))
    out.pages.append(image2.pages[0])
    buf = io.BytesIO()
    out.save(buf, linearize=False)
    out.close()
    image_pdf.close()
    text2.close()
    image2.close()
    return buf.getvalue()


def test_partial_mrc_skipped_indices_are_sorted_and_non_contiguous() -> None:
    """4-page text/image/text/image PDF: skipped indices must be (0, 2)
    in sorted order. Catches off-by-one and set-iteration-order bugs in
    the _verbatim_pages → tuple(sorted(...)) pipeline.
    """
    pdf_bytes = _make_4page_alternating_pdf()
    _, report = compress(pdf_bytes, options=CompressOptions())
    assert report.status != "passed_through"
    assert report.pages_skipped_verbatim == (0, 2)
    # Aggregate warning carries the count.
    assert "pages-skipped-verbatim-2" in report.warnings


def test_compress_stream_routes_through_the_gate() -> None:
    """compress_stream() uses compress() under the hood; verify the gate
    fires through the streaming entry point too.

    compress_stream signature is `(input_stream, output_stream, options=None) -> CompressReport`
    (per pdf_smasher/__init__.py:1322-1331) — note: TWO positional streams
    and a single CompressReport return.
    """
    from pdf_smasher import compress_stream

    pdf_bytes = _make_text_only_pdf()
    out_buf = io.BytesIO()
    report = compress_stream(io.BytesIO(pdf_bytes), out_buf, options=CompressOptions())
    assert any("passthrough-no-image-content" in w for w in report.warnings)
    assert out_buf.getvalue() == pdf_bytes  # whole-doc passthrough returns input unchanged
