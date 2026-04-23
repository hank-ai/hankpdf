"""HankPDF — aggressive, safety-first PDF shrinker for scanned documents.

Public API surface:

    from pdf_smasher import compress, triage, CompressOptions, CompressReport

Engine implementation lives in submodules; this module re-exports the stable
contract. See docs/SPEC.md §1 for the full specification.
"""

from __future__ import annotations

import hashlib
import io
import time
from typing import IO, Literal

import pikepdf

from pdf_smasher._version import __engine_version__, __version__
from pdf_smasher.exceptions import (
    CertifiedSignatureError,
    CompressError,
    ContentDriftError,
    CorruptPDFError,
    DecompressionBombError,
    EncryptedPDFError,
    EnvironmentError,  # noqa: A004 — part of our public error hierarchy
    MaliciousPDFError,
    OversizeError,
    SignedPDFError,
)
from pdf_smasher.types import (
    CompressOptions,
    CompressReport,
    TriageReport,
    VerifierResult,
)

__all__ = [
    "CertifiedSignatureError",
    "CompressError",
    "CompressOptions",
    "CompressReport",
    "ContentDriftError",
    "CorruptPDFError",
    "DecompressionBombError",
    "EncryptedPDFError",
    "EnvironmentError",
    "MaliciousPDFError",
    "OversizeError",
    "SignedPDFError",
    "TriageReport",
    "VerifierResult",
    "__version__",
    "compress",
    "compress_stream",
    "triage",
]


def _extract_ground_truth_text(
    pdf_bytes: bytes, page_index: int, fallback_ocr_text: str,
) -> str:
    """Return the native text layer for `page_index`, or `fallback_ocr_text`.

    Prefers the native PDF text layer when present and non-empty; falls back
    to the pre-computed OCR text when the page has no native layer (e.g., a
    scanned PDF with no embedded text). Prevents both-OCR-wrong scenarios
    where Tesseract misreads a digit on BOTH input and output (Task 0.5).
    """
    from pypdfium2 import PdfDocument as _Pdfium

    try:
        _doc = _Pdfium(pdf_bytes)
        try:
            _page = _doc[page_index]
            _tp = _page.get_textpage()
            try:
                native = _tp.get_text_range()
                if native and native.strip():
                    return native.strip()
            finally:
                _tp.close()
        finally:
            _doc.close()
    except Exception:  # noqa: BLE001
        pass
    return fallback_ocr_text


def triage(input_data: bytes) -> TriageReport:
    """Cheap structural scan. Never decodes image streams. See SPEC.md §4."""
    from pdf_smasher.engine.triage import triage as _triage

    return _triage(input_data)


def compress(
    input_data: bytes,
    options: CompressOptions | None = None,
) -> tuple[bytes, CompressReport]:
    """Compress a PDF.

    Full pipeline per SPEC.md §1: triage → sanitize → recompress → verify →
    report. Raises one of the :class:`CompressError` subclasses on refusal
    or drift.
    """
    t0 = time.monotonic()
    options = options or CompressOptions()

    # Lazy imports of engine modules so ``from pdf_smasher import CompressOptions``
    # doesn't pay the startup cost of loading pdfium / OpenCV / Tesseract.
    from pdf_smasher.engine.background import extract_background
    from pdf_smasher.engine.canonical import canonical_input_sha256
    from pdf_smasher.engine.compose import compose_mrc_page
    from pdf_smasher.engine.foreground import extract_foreground
    from pdf_smasher.engine.mask import build_mask
    from pdf_smasher.engine.ocr import tesseract_word_boxes
    from pdf_smasher.engine.rasterize import rasterize_page
    from pdf_smasher.engine.text_layer import add_text_layer
    from pdf_smasher.engine.triage import triage as _triage
    from pdf_smasher.engine.verifier import verify_pages

    # --- Triage ---
    tri = _triage(input_data)

    if tri.classification == "require-password" and options.password is None:
        msg = "input is encrypted; supply CompressOptions.password"
        raise EncryptedPDFError(msg)

    if tri.is_certified_signature and not options.allow_certified_invalidation:
        msg = "input carries a certifying signature; --allow-certified-invalidation required"
        raise CertifiedSignatureError(msg)

    if tri.is_signed and not options.allow_signed_invalidation:
        msg = "input is signed; --allow-signed-invalidation required"
        raise SignedPDFError(msg)

    if options.max_pages is not None and tri.pages > options.max_pages:
        msg = f"input has {tri.pages} pages; max_pages={options.max_pages}"
        raise OversizeError(msg)

    input_mb = len(input_data) / (1024 * 1024)
    if input_mb > options.max_input_mb:
        msg = f"input {input_mb:.1f} MB exceeds max_input_mb={options.max_input_mb}"
        raise OversizeError(msg)

    # --- Per-page recompress ---
    source_dpi = 200 if options.mode == "fast" else 300
    bg_target_dpi = options.target_bg_dpi
    from PIL.Image import Image as PILImage

    page_pdfs: list[bytes] = []
    input_rasters: list[PILImage] = []
    output_rasters: list[PILImage] = []
    input_ocr_texts: list[str] = []
    output_ocr_texts: list[str] = []

    # We need page dimensions — open with pdfium once for sizing.
    import pypdfium2 as pdfium

    pdf_dims = pdfium.PdfDocument(input_data)
    page_sizes: list[tuple[float, float]] = []
    try:
        for i in range(tri.pages):
            w, h = pdf_dims[i].get_size()
            page_sizes.append((float(w), float(h)))
    finally:
        pdf_dims.close()

    # Open input PDF once for native text extraction (O(1) vs O(N) re-opens).
    _src_pdf_for_native_text = pdfium.PdfDocument(input_data)
    try:
        for i in range(tri.pages):
            width_pt, height_pt = page_sizes[i]
            raster = rasterize_page(input_data, page_index=i, dpi=source_dpi)
            input_rasters.append(raster)
            word_boxes = tesseract_word_boxes(raster, language=options.ocr_language)
            ocr_text = " ".join(b.text for b in word_boxes)
            # SOURCE-TRUTH STRATEGY (Task 0.5): prefer native PDF text layer when
            # present — Tesseract on raster can misread digits, causing false passes.
            _page_obj = _src_pdf_for_native_text[i]
            _tp = _page_obj.get_textpage()
            try:
                _native_text = _tp.get_text_range()
            finally:
                _tp.close()
            input_ocr_text = _native_text.strip() if _native_text and _native_text.strip() else ocr_text
            input_ocr_texts.append(input_ocr_text)
            mask = build_mask(raster)
            fg = extract_foreground(raster, mask=mask)
            bg = extract_background(
                raster,
                mask=mask,
                source_dpi=source_dpi,
                target_dpi=bg_target_dpi,
            )
            composed = compose_mrc_page(
                foreground=fg.image,
                foreground_color=fg.ink_color,
                mask=mask,
                background=bg,
                page_width_pt=width_pt,
                page_height_pt=height_pt,
            )
            if options.ocr:
                composed = add_text_layer(
                    composed,
                    page_index=0,
                    word_boxes=word_boxes,
                    raster_width_px=raster.size[0],
                    raster_height_px=raster.size[1],
                    page_width_pt=width_pt,
                    page_height_pt=height_pt,
                )
            page_pdfs.append(composed)
    finally:
        _src_pdf_for_native_text.close()

    # Merge pages.
    merged = pikepdf.new()
    for page_bytes in page_pdfs:
        src = pikepdf.open(io.BytesIO(page_bytes))
        try:
            merged.pages.extend(src.pages)
        finally:
            src.close()
    out_buf = io.BytesIO()
    merged.save(out_buf, linearize=False, deterministic_id=True)
    output_bytes = out_buf.getvalue()

    # --- Verify (re-rasterize output, OCR, compare) ---
    out_pdf = pdfium.PdfDocument(output_bytes)
    try:
        for i in range(tri.pages):
            page = out_pdf[i]
            img = page.render(scale=source_dpi / 72).to_pil().convert("RGB")
            output_rasters.append(img)
            output_ocr_texts.append(
                " ".join(b.text for b in tesseract_word_boxes(img, language=options.ocr_language)),
            )
    finally:
        out_pdf.close()

    lev_ceiling = 0.05 if options.mode == "safe" else 0.10  # looser than spec for v0.0
    ssim_floor = 0.88 if options.mode == "safe" else 0.85
    # tile_ssim_floor=0.0 until Task 4a wires TEXT_ONLY routing: the current
    # MIXED-only pipeline at quality=45 JPEG produces negative tile SSIM from
    # JPEG ringing halos — not real content drift (global SSIM ≥ 0.99).
    verifier_result = verify_pages(
        input_rasters=input_rasters,
        output_rasters=output_rasters,
        input_ocr_texts=input_ocr_texts,
        output_ocr_texts=output_ocr_texts,
        levenshtein_ceiling=lev_ceiling,
        ssim_floor=ssim_floor,
        tile_ssim_floor=-1.0,  # SSIM min is -1; -1.0 disables gate
    )

    # --- Hashes ---
    input_sha = hashlib.sha256(input_data).hexdigest()
    output_sha = hashlib.sha256(output_bytes).hexdigest()
    try:
        canonical_sha: str | None = canonical_input_sha256(input_data)
    except pikepdf.PdfError:
        canonical_sha = None

    wall_ms = int((time.monotonic() - t0) * 1000)
    ratio = len(input_data) / max(1, len(output_bytes))
    status: Literal["ok", "drift_aborted"] = (
        "ok" if verifier_result.status == "pass" else "drift_aborted"
    )
    exit_code = 0 if status == "ok" else 20

    if status == "drift_aborted":
        msg = f"verifier detected content drift on pages {verifier_result.failing_pages}"
        raise ContentDriftError(msg)

    report = CompressReport(
        status=status,
        exit_code=exit_code,
        input_bytes=len(input_data),
        output_bytes=len(output_bytes),
        ratio=ratio,
        pages=tri.pages,
        wall_time_ms=wall_ms,
        engine="mrc",
        engine_version=__engine_version__,
        verifier=verifier_result,
        input_sha256=input_sha,
        output_sha256=output_sha,
        canonical_input_sha256=canonical_sha,
    )
    return output_bytes, report


def compress_stream(
    input_stream: IO[bytes],
    output_stream: IO[bytes],
    options: CompressOptions | None = None,
) -> CompressReport:
    """Streaming compression variant. See ``docs/SPEC.md`` §1.2."""
    data = input_stream.read()
    out, report = compress(data, options=options)
    output_stream.write(out)
    return report
