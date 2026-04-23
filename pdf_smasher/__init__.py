"""HankPDF — aggressive, safety-first PDF shrinker for scanned documents.

Public API surface:

    from pdf_smasher import compress, triage, CompressOptions, CompressReport

Engine implementation lives in submodules; this module re-exports the stable
contract. See docs/SPEC.md §1 for the full specification.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import threading
import time
from collections.abc import Callable
from typing import IO, Any, Literal

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


_JBIG2_CASCADE_STATE = threading.local()


_CHROMA_TO_PIL: dict[str, int] = {"4:4:4": 0, "4:2:2": 1, "4:2:0": 2}

# Per-page ratio above which the tile-SSIM gate tightens to the safe floor.
# Chosen at 200x because TEXT_ONLY hits 100-500x legitimately via JBIG2 on
# dense text; anything above that on MIXED or PHOTO_ONLY is suspicious.
_ANOMALY_RATIO_THRESHOLD = 200.0


def _mrc_compose(
    raster: Any,
    mask: Any,
    width_pt: float,
    height_pt: float,
    bg_target_dpi: int,
    source_dpi: int,
    *,
    bg_codec: Literal["jpeg", "jpeg2000"] = "jpeg",
    bg_jpeg_quality: int = 45,
    bg_subsampling: int = 0,
) -> bytes:
    """MRC composition helper (Task 4a). Caller decides bg_color_mode via raster check."""
    from pdf_smasher.engine.background import extract_background
    from pdf_smasher.engine.compose import compose_mrc_page
    from pdf_smasher.engine.foreground import extract_foreground, is_effectively_monochrome

    fg = extract_foreground(raster, mask=mask)
    bg = extract_background(
        raster,
        mask=mask,
        source_dpi=source_dpi,
        target_dpi=bg_target_dpi,
    )
    bg_color_mode: Literal["rgb", "grayscale"] = (
        "grayscale" if is_effectively_monochrome(raster) else "rgb"
    )
    return compose_mrc_page(
        foreground=fg.image,
        foreground_color=fg.ink_color,
        mask=mask,
        background=bg,
        page_width_pt=width_pt,
        page_height_pt=height_pt,
        bg_color_mode=bg_color_mode,
        bg_codec=bg_codec,
        bg_jpeg_quality=bg_jpeg_quality,
        bg_subsampling=bg_subsampling,
    )


def _extract_ground_truth_text(
    pdf_bytes: bytes,
    page_index: int,
    fallback_ocr_text: str,
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
                    return str(native).strip()
            finally:
                _tp.close()
        finally:
            _doc.close()
    except Exception:  # noqa: BLE001, S110 — best-effort native-text probe; any error → fall back to OCR.
        pass
    return fallback_ocr_text


def triage(input_data: bytes) -> TriageReport:
    """Cheap structural scan. Never decodes image streams. See SPEC.md §4."""
    from pdf_smasher.engine.triage import triage as _triage

    return _triage(input_data)


def compress(
    input_data: bytes,
    options: CompressOptions | None = None,
    *,
    progress_callback: Callable[[str], None] | None = None,
) -> tuple[bytes, CompressReport]:
    """Compress a PDF.

    Full pipeline per SPEC.md §1: triage → sanitize → recompress → verify →
    report. Raises one of the :class:`CompressError` subclasses on refusal
    or drift.

    ``progress_callback`` is an optional ``fn(message: str) -> None`` hook
    invoked at pipeline milestones (triage complete, per-page dispatch and
    completion, merge, verify, final). The CLI wires this to stderr unless
    ``--quiet`` is passed. No PHI is included in messages — only page
    indices, strategy names, and byte counts.
    """
    t0 = time.monotonic()
    options = options or CompressOptions()

    def _emit(msg: str) -> None:
        if progress_callback is not None:
            progress_callback(msg)

    # GUARD: legal_codec_profile (CCITT G4) is reserved for a later phase.
    # Placed before triage so the error is actionable even on empty input.
    if options.legal_codec_profile:
        msg = (
            "legal_codec_profile (CCITT G4 fallback) is not implemented in this "
            "build. Use legal_codec_profile=None and --engine mrc for Phase-2b "
            "outputs; tracked for a later phase."
        )
        raise NotImplementedError(msg)

    # Lazy imports of engine modules so ``from pdf_smasher import CompressOptions``
    # doesn't pay the startup cost of loading pdfium / OpenCV / Tesseract.
    import numpy as np

    from pdf_smasher.engine.canonical import canonical_input_sha256
    from pdf_smasher.engine.compose import compose_photo_only_page, compose_text_only_page
    from pdf_smasher.engine.foreground import (
        detect_paper_color,
        extract_foreground,
        is_effectively_monochrome,
    )
    from pdf_smasher.engine.mask import build_mask
    from pdf_smasher.engine.ocr import tesseract_word_boxes
    from pdf_smasher.engine.rasterize import rasterize_page
    from pdf_smasher.engine.strategy import PageStrategy, classify_page
    from pdf_smasher.engine.text_layer import add_text_layer
    from pdf_smasher.engine.triage import triage as _triage
    from pdf_smasher.engine.verifier import (
        _DEFAULT_TILE_SSIM_FLOOR_SAFE,
        _DEFAULT_TILE_SSIM_FLOOR_STANDARD,
        _VerifierAggregator,
        verify_single_page,
    )

    # --- Triage ---
    _emit(f"triage: {len(input_data):,} bytes input")
    tri = _triage(input_data)
    _emit(
        f"triage complete: {tri.pages} pages, classification={tri.classification}, "
        f"encrypted={tri.is_encrypted}, signed={tri.is_signed}",
    )

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
    is_safe = options.mode == "safe"

    import pypdfium2 as pdfium

    page_pdfs: list[bytes] = []

    # We need page dimensions — open with pdfium once for sizing.
    pdf_dims = pdfium.PdfDocument(input_data)
    page_sizes: list[tuple[float, float]] = []
    try:
        for i in range(tri.pages):
            w, h = pdf_dims[i].get_size()
            page_sizes.append((float(w), float(h)))
    finally:
        pdf_dims.close()

    lev_ceiling = 0.02 if is_safe else 0.05
    ssim_floor = 0.92

    warnings_list: list[str] = []
    if shutil.which("jbig2") is None:
        warnings_list.append("jbig2enc-unavailable-using-flate-fallback")

    # Fast mode forces JPEG on the bg path: JPEG2000 via Pillow/OpenJPEG is
    # ~1-2 s/page at 300 DPI (+3-6 min on a 200-page doc). Users who asked
    # for fast explicitly traded ratio for speed — don't silently undo that.
    # Emit a warning so a user who set bg_codec=jpeg2000 isn't surprised.
    effective_bg_codec = options.bg_codec
    if options.mode == "fast" and options.bg_codec == "jpeg2000":
        warnings_list.append("bg-codec-jpeg2000-demoted-fast-mode")
        effective_bg_codec = "jpeg"
    verifier_agg = _VerifierAggregator()
    strategy_counts: dict[str, int] = {
        "text_only": 0,
        "photo_only": 0,
        "mixed": 0,
        "already_optimized": 0,
    }

    # Open input PDF once for native text extraction.
    _src_pdf_for_native_text = pdfium.PdfDocument(input_data)
    try:
        for i in range(tri.pages):
            width_pt, height_pt = page_sizes[i]
            _emit(f"page {i + 1}/{tri.pages}: rasterizing + OCR ({source_dpi} DPI)")
            try:
                raster = rasterize_page(input_data, page_index=i, dpi=source_dpi)
                word_boxes = tesseract_word_boxes(raster, language=options.ocr_language)
                ocr_text = " ".join(b.text for b in word_boxes)

                # SOURCE-TRUTH: prefer native PDF text layer when present.
                _page_obj = _src_pdf_for_native_text[i]
                _tp = _page_obj.get_textpage()
                try:
                    _native_text = _tp.get_text_range()
                finally:
                    _tp.close()
                input_ocr_text = (
                    _native_text.strip() if _native_text and _native_text.strip() else ocr_text
                )

                mask = build_mask(raster)
                mask_arr = np.asarray(mask.convert("1"), dtype=bool)
                mask_coverage = float(mask_arr.sum()) / max(1, mask_arr.size)

                strategy = classify_page(raster, mask_coverage_fraction=mask_coverage)

                if options.force_monochrome:
                    from pdf_smasher.engine.verifier import _page_has_color

                    if _page_has_color(raster):
                        warnings_list.append(f"page-{i + 1}-color-detected-in-monochrome-mode")
                    if strategy not in (PageStrategy.ALREADY_OPTIMIZED, PageStrategy.PHOTO_ONLY):
                        strategy = PageStrategy.TEXT_ONLY
                    # PHOTO_ONLY + force_monochrome: keep PHOTO_ONLY but grayscale
                    # (handled in the PHOTO_ONLY branch via _photo_bg_color_mode).

                if strategy == PageStrategy.ALREADY_OPTIMIZED:
                    msg = (
                        f"page {i + 1}: classify_page returned ALREADY_OPTIMIZED but "
                        "compress() has no handler for this value."
                    )
                    raise AssertionError(msg)  # noqa: TRY301 — defensive guard; classify_page currently never emits this
                if strategy == PageStrategy.TEXT_ONLY:
                    if not options.force_monochrome and not is_effectively_monochrome(raster):
                        warnings_list.append(
                            f"page-{i + 1}-text-only-demoted-to-mixed-color-detected"
                        )
                        strategy = PageStrategy.MIXED
                        _JBIG2_CASCADE_STATE.tripped = False
                        composed = _mrc_compose(
                            raster,
                            mask,
                            width_pt,
                            height_pt,
                            bg_target_dpi,
                            source_dpi,
                            bg_codec=effective_bg_codec,
                            bg_jpeg_quality=options.target_color_quality,
                            bg_subsampling=_CHROMA_TO_PIL[options.bg_chroma_subsampling],
                        )
                    else:
                        fg = extract_foreground(raster, mask=mask)
                        paper = detect_paper_color(raster)
                        composed = compose_text_only_page(
                            mask=mask,
                            foreground_color=fg.ink_color,
                            paper_color=paper,
                            page_width_pt=width_pt,
                            page_height_pt=height_pt,
                        )
                elif strategy == PageStrategy.PHOTO_ONLY:
                    _photo_bg_color_mode: Literal["rgb", "grayscale"] = (
                        "grayscale"
                        if (options.force_monochrome or is_effectively_monochrome(raster))
                        else "rgb"
                    )
                    composed = compose_photo_only_page(
                        raster=raster,
                        page_width_pt=width_pt,
                        page_height_pt=height_pt,
                        target_dpi=options.photo_target_dpi,
                        bg_color_mode=_photo_bg_color_mode,
                        bg_codec=effective_bg_codec,
                        jpeg_quality=options.target_color_quality,
                        subsampling=_CHROMA_TO_PIL[options.bg_chroma_subsampling],
                    )
                else:  # MIXED
                    _JBIG2_CASCADE_STATE.tripped = False
                    composed = _mrc_compose(
                        raster,
                        mask,
                        width_pt,
                        height_pt,
                        bg_target_dpi,
                        source_dpi,
                        bg_codec=effective_bg_codec,
                        bg_jpeg_quality=options.target_color_quality,
                        bg_subsampling=_CHROMA_TO_PIL[options.bg_chroma_subsampling],
                    )
                    if getattr(_JBIG2_CASCADE_STATE, "tripped", False):
                        warnings_list.append(f"page-{i + 1}-jbig2-fallback-to-flate")

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

                # Streaming verify: rasterize output, compare, drop rasters.
                output_raster = rasterize_page(composed, page_index=0, dpi=source_dpi)
                output_ocr_text = " ".join(
                    b.text
                    for b in tesseract_word_boxes(output_raster, language=options.ocr_language)
                )
                per_page_input_estimate = raster.width * raster.height * 3
                per_page_ratio = per_page_input_estimate / max(1, len(composed))
                anomalous = (
                    per_page_ratio > _ANOMALY_RATIO_THRESHOLD and strategy != PageStrategy.TEXT_ONLY
                )
                if strategy == PageStrategy.MIXED:
                    # Tile SSIM gates JPEG ringing artifacts in the MRC background layer.
                    page_tile_ssim_floor = (
                        _DEFAULT_TILE_SSIM_FLOOR_SAFE
                        if (anomalous or is_safe)
                        else _DEFAULT_TILE_SSIM_FLOOR_STANDARD
                    )
                    page_ssim_floor = ssim_floor
                    page_lev_ceiling = lev_ceiling
                elif strategy == PageStrategy.PHOTO_ONLY:
                    # PHOTO_ONLY pages are aggressively lossy — quality 45 at target_dpi
                    # produces legitimate SSIM well below the 0.92 MRC floor.
                    # OCR on pure-photo pages produces garbage on both sides; skip it.
                    page_tile_ssim_floor = -1.0
                    page_ssim_floor = 0.5
                    page_lev_ceiling = 1.0
                else:
                    # TEXT_ONLY / ALREADY_OPTIMIZED: SSIM is still meaningful (binary
                    # JBIG2 on white bg scores well globally); tile SSIM is not.
                    page_tile_ssim_floor = -1.0
                    page_ssim_floor = ssim_floor
                    page_lev_ceiling = lev_ceiling
                if anomalous:
                    warnings_list.append(
                        f"page-{i + 1}-anomalous-ratio-{per_page_ratio:.0f}x-safe-verify"
                    )
                per_page_verdict = verify_single_page(
                    input_raster=raster,
                    output_raster=output_raster,
                    input_ocr_text=input_ocr_text,
                    output_ocr_text=output_ocr_text,
                    lev_ceiling=page_lev_ceiling,
                    ssim_floor=page_ssim_floor,
                    tile_ssim_floor=page_tile_ssim_floor,
                    check_color_preserved=not options.force_monochrome,
                )
                verifier_agg.merge(i, per_page_verdict)
                del raster, output_raster, input_ocr_text, output_ocr_text
                page_pdfs.append(composed)
                strategy_counts[strategy.name.lower()] += 1
                _emit(
                    f"page {i + 1}/{tri.pages} done: strategy={strategy.name.lower()}, "
                    f"ratio={per_page_ratio:.1f}x, "
                    f"verifier={'pass' if per_page_verdict.passed else 'fail'}",
                )

            except KeyboardInterrupt:
                page_pdfs.clear()
                raise
            except AssertionError, CompressError:
                raise
            except Exception as e:
                msg = f"compression failed on page {i + 1}/{tri.pages}: {e}"
                raise CompressError(msg) from e
    finally:
        _src_pdf_for_native_text.close()

    assert len(page_pdfs) == tri.pages, (
        f"page_pdfs has {len(page_pdfs)} entries but input had {tri.pages} pages"
    )

    n_color_discarded = sum(1 for w in warnings_list if "color-detected-in-monochrome-mode" in w)
    if n_color_discarded > 0:
        warnings_list.append(f"force-monochrome-discarded-color-on-{n_color_discarded}-pages")

    # Merge pages.
    _emit(f"merging {tri.pages} pages into output PDF")
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
    _emit(
        f"merge complete: {len(output_bytes):,} bytes "
        f"(ratio {len(input_data) / max(1, len(output_bytes)):.2f}x)",
    )

    verifier_result = verifier_agg.result()
    if verifier_result.status == "fail":
        if options.mode != "fast":
            msg = (
                f"content drift detected on pages {list(verifier_result.failing_pages)}: "
                f"ocr_lev={verifier_result.ocr_levenshtein:.4f}, "
                f"ssim_global={verifier_result.ssim_global:.4f}, "
                f"ssim_tile_min={verifier_result.ssim_min_tile:.4f}, "
                f"digit_multiset_match={verifier_result.digit_multiset_match}, "
                f"color_preserved={verifier_result.color_preserved}"
            )
            raise ContentDriftError(msg)
        warnings_list.append(f"verifier-fail-fast-mode-pages-{list(verifier_result.failing_pages)}")

    # --- Hashes ---
    input_sha = hashlib.sha256(input_data).hexdigest()
    output_sha = hashlib.sha256(output_bytes).hexdigest()
    try:
        canonical_sha: str | None = canonical_input_sha256(input_data)
    except pikepdf.PdfError:
        canonical_sha = None

    wall_ms = int((time.monotonic() - t0) * 1000)
    ratio = len(input_data) / max(1, len(output_bytes))
    status: Literal["ok", "drift_aborted"] = "ok"
    exit_code = 0

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
        warnings=tuple(warnings_list),
        strategy_distribution=dict(strategy_counts),
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
