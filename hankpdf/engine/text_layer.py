"""Add an invisible OCR text layer to an existing PDF page.

Uses the standard Helvetica Type 1 font (one of the 14 standard fonts every
PDF reader ships) so we don't embed font data. Text is painted with
rendering mode 3 (invisible) so it's searchable and selectable but has no
visual effect.

Coordinates: OCR word boxes are in *raster pixels* (top-left origin, Y
down). PDF text coordinates are in *points* (bottom-left origin, Y up).
We convert one to the other.

Source of word boxes:

- :func:`hankpdf.engine.ocr.tesseract_word_boxes` — re-OCR the raster
  via Tesseract. Used for true scans (no upstream text layer).
- :func:`extract_native_word_boxes` (this module) — read text + bboxes
  from the input PDF's existing content stream via pdfium. Faithful and
  Tesseract-free for inputs that already have a searchable text layer.
"""

from __future__ import annotations

import ctypes
import io
from collections.abc import Sequence
from typing import Any

import pikepdf
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from hankpdf.engine.ocr import WordBox

_HELVETICA_FONT_NAME = pikepdf.Name("/F-HankPDF-Invisible")

# pdfium's per-glyph style probes are a best-effort fidelity nicety, never a
# correctness requirement: any glyph the font/matrix APIs can't describe (and
# any older pdfium build missing a symbol) must degrade to ``None``, not raise
# and abort extraction. These are the failures we tolerate per probe.
_STYLE_PROBE_ERRORS = (ctypes.ArgumentError, AttributeError, OSError, ValueError, TypeError)


def _escape_pdf_string(text: str) -> str:
    """Escape text for safe inclusion in a PDF literal string (parentheses)."""
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _build_text_ops(
    word_boxes: Sequence[WordBox],
    *,
    raster_width_px: int,
    raster_height_px: int,
    page_width_pt: float,
    page_height_pt: float,
) -> bytes:
    """Build a PDF content-stream fragment painting invisible OCR text."""
    if not word_boxes:
        return b""

    sx = page_width_pt / raster_width_px  # px -> pt for X
    sy = page_height_pt / raster_height_px  # px -> pt for Y (absolute value)
    parts: list[str] = ["q", "BT", "3 Tr"]  # rendering mode 3: invisible
    for box in word_boxes:
        if not box.text.strip() or box.width <= 0 or box.height <= 0:
            continue
        # Convert bounding box to PDF point coordinates.
        # Baseline of the text goes near the bottom of the bounding box; we put
        # it exactly at the bottom for a simple, consistent mapping.
        x_pt = box.x * sx
        # PDF Y axis is inverted relative to raster; raster y=0 is top, PDF
        # y=page_height is top. Bottom of the bbox in raster == top of the
        # bbox in PDF.
        baseline_y_raster = box.y + box.height
        y_pt = page_height_pt - (baseline_y_raster * sy)
        # Font size: scale so that the rendered text fits the bbox height.
        font_size_pt = box.height * sy
        # Horizontal scaling: stretch the glyph advance so the full word
        # covers the bbox width. Helvetica avg glyph is ~0.5em wide; we use
        # PDF's Tz (horizontal scaling) to fit.
        # Approx measured width at 1pt font = 0.5 * len(text) pt.
        # We want it to equal bbox width = box.width * sx pt.
        target_width_pt = box.width * sx
        # Avoid divide-by-zero / absurd scaling on very short text.
        approx_text_width_pt = max(0.5 * len(box.text) * font_size_pt, 1e-3)
        hscale_percent = (target_width_pt / approx_text_width_pt) * 100
        parts.append(f"/F-HankPDF-Invisible {font_size_pt:.3f} Tf")
        parts.append(f"{hscale_percent:.2f} Tz")
        parts.append(f"1 0 0 1 {x_pt:.3f} {y_pt:.3f} Tm")
        parts.append(f"({_escape_pdf_string(box.text)}) Tj")
    parts.extend(("ET", "Q"))
    return ("\n".join(parts) + "\n").encode("ascii", errors="replace")


def add_text_layer(
    pdf_bytes: bytes,
    *,
    page_index: int,
    word_boxes: Sequence[WordBox],
    raster_width_px: int,
    raster_height_px: int,
    page_width_pt: float,
    page_height_pt: float,
) -> bytes:
    """Return a new PDF bytestream with an invisible OCR layer on ``page_index``."""
    with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[page_index]

        # Register Helvetica as a standard Type 1 font.
        font_obj = pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name.Helvetica,
            Encoding=pikepdf.Name.WinAnsiEncoding,
        )

        # Ensure /Resources dict exists; merge font.
        if "/Resources" not in page:
            page.Resources = pikepdf.Dictionary()
        if "/Font" not in page.Resources:
            page.Resources.Font = pikepdf.Dictionary()
        page.Resources.Font["/F-HankPDF-Invisible"] = font_obj
        _ = _HELVETICA_FONT_NAME  # ensure name exists on module

        ops = _build_text_ops(
            word_boxes,
            raster_width_px=raster_width_px,
            raster_height_px=raster_height_px,
            page_width_pt=page_width_pt,
            page_height_pt=page_height_pt,
        )
        if ops:
            new_stream = pdf.make_stream(ops)
            # Content can be absent (blank page), a single stream, or an
            # array of streams. Normalize to an array.
            existing = page.obj.get("/Contents")
            if existing is None:
                page.Contents = new_stream
            elif isinstance(existing, pikepdf.Array):
                existing.append(new_stream)
            else:
                page.Contents = pikepdf.Array([existing, new_stream])

        buf = io.BytesIO()
        pdf.save(buf, linearize=False, deterministic_id=True)
        return buf.getvalue()


# Tuning constants for is_native_text_decent. Centralized so they're greppable
# and so adjustments can be made without scattering magic numbers.
_NATIVE_DECENCY_SPARSE_THRESHOLD = (
    30  # below this many chars, treat the page as too sparse to judge
)
_NATIVE_DECENCY_MIN_ALPHA_RATIO = 0.5  # at least half the chars must be letters or whitespace
_NATIVE_DECENCY_MIN_AVG_WORD_LEN = 2.0
_NATIVE_DECENCY_MAX_AVG_WORD_LEN = 12.0
_NATIVE_DECENCY_MAX_SINGLE_CHAR_RATIO = 0.4  # ">40% single-char words" = "S c a l i n g" pattern


def is_native_text_decent(boxes: Sequence[WordBox]) -> bool:
    """Heuristic: is a native text layer reasonable enough to keep?

    Returns ``True`` for text that looks like real words; ``False`` for
    OCR-garbage signatures. Used to decide whether ``--ocr`` should
    accept the existing text or replace it with a fresh Tesseract pass.

    Rejects:
      - Mostly-non-alphabetic content (punctuation/symbol noise) — e.g.
        a corrupted text layer where most chars are ``?`` or unicode
        replacement markers.
      - Average word length outside 2-12 chars — gibberish OCR often
        produces single-char tokens or long runs of garbage.
      - High single-char-word ratio (>40%) — the "S c a l i n g" pattern
        you get when an OCR engine treats every glyph as its own word
        because it couldn't infer word boundaries.

    Sparse pages (covers, dividers) with very little text pass — light
    text density alone isn't a quality signal. The minimum gate is that
    SOME text exists; below 30 chars we return ``True`` rather than
    forcing a Tesseract pass on a near-blank page.
    """
    if not boxes:
        return False
    full_text = " ".join(b.text for b in boxes)
    if len(full_text) < _NATIVE_DECENCY_SPARSE_THRESHOLD:
        return True  # too sparse to judge; keep what's there
    alpha_or_space = sum(1 for c in full_text if c.isalpha() or c.isspace())
    if alpha_or_space / len(full_text) < _NATIVE_DECENCY_MIN_ALPHA_RATIO:
        return False
    words = [w for w in full_text.split() if w.isalpha()]
    if not words:
        return False
    avg_len = sum(len(w) for w in words) / len(words)
    if avg_len < _NATIVE_DECENCY_MIN_AVG_WORD_LEN or avg_len > _NATIVE_DECENCY_MAX_AVG_WORD_LEN:
        return False
    single_char_ratio = sum(1 for w in words if len(w) == 1) / len(words)
    return single_char_ratio <= _NATIVE_DECENCY_MAX_SINGLE_CHAR_RATIO


def extract_native_word_boxes(
    pdf_bytes: bytes,
    *,
    page_index: int,
    raster_width_px: int,
    raster_height_px: int,
    password: str | None = None,
) -> list[WordBox]:
    """Read text + bounding boxes from an input page's existing text layer.

    Walks pdfium's per-character API and groups consecutive non-whitespace
    chars into word-level :class:`WordBox` records. Coordinates are
    converted from PDF points (bottom-left origin) to raster pixels
    (top-left origin) so the result is drop-in compatible with
    :func:`add_text_layer`.

    A new word starts on:
      - whitespace (the whitespace itself is not emitted)
      - a Y-baseline jump greater than half the current word's height
        (line break)
      - a horizontal gap to the next char larger than ``GAP_FACTOR x
        the current word's height`` (column / wide-spacing break)

    Returns an empty list when the page has no text layer (true scan).
    Callers should fall back to :func:`tesseract_word_boxes` in that
    case. Confidence is set to ``100.0`` — native extraction is faithful
    by construction (no recognition step).
    """
    pdf = pdfium.PdfDocument(pdf_bytes, password=password)
    try:
        page = pdf[page_index]
        page_width_pt, page_height_pt = page.get_size()
        if page_width_pt <= 0 or page_height_pt <= 0:
            return []
        tp = page.get_textpage()
        try:
            return _walk_chars_into_words(
                tp,
                raster_width_px=raster_width_px,
                raster_height_px=raster_height_px,
                page_width_pt=page_width_pt,
                page_height_pt=page_height_pt,
            )
        finally:
            tp.close()
    finally:
        pdf.close()


def _native_char_style(
    tp_raw: object,
    index: int,
    *,
    page_height_pt: float,
    sy: float,
) -> dict[str, Any]:
    """Probe one glyph's font/size/weight/colour/baseline via pdfium's raw API.

    Returns the optional style fields of :class:`WordBox` as a kwargs dict.
    Best-effort by contract: each probe is guarded independently so a single
    unsupported field degrades to ``None`` without losing the others, and no
    probe ever raises out of here (style is fidelity, never correctness).

    ``baseline_y`` is derived from the text matrix: the text-space origin maps
    to the matrix translation ``(e, f)`` in page points, which is the glyph's
    baseline. We convert that to raster pixels (top-left origin) so it shares the
    coordinate frame of :class:`WordBox`'s ``x``/``y``.
    """
    flags = ctypes.c_int(0)
    name: str | None = None
    try:
        needed = pdfium_c.FPDFText_GetFontInfo(tp_raw, index, None, 0, ctypes.byref(flags))
        if needed and needed > 1:
            buf = ctypes.create_string_buffer(needed)
            pdfium_c.FPDFText_GetFontInfo(tp_raw, index, buf, needed, ctypes.byref(flags))
            # pdfium NUL-terminates; `needed` counts the terminator.
            name = buf.raw[: needed - 1].decode("utf-8", "replace") or None
    except _STYLE_PROBE_ERRORS:
        name = None

    try:
        size = float(pdfium_c.FPDFText_GetFontSize(tp_raw, index))
        size_pt: float | None = size if size > 0 else None
    except _STYLE_PROBE_ERRORS:
        size_pt = None

    try:
        weight = int(pdfium_c.FPDFText_GetFontWeight(tp_raw, index))
        weight_val: int | None = weight if weight > 0 else None
    except _STYLE_PROBE_ERRORS:
        weight_val = None

    color: tuple[int, int, int] | None = None
    try:
        r = ctypes.c_uint()
        g = ctypes.c_uint()
        b = ctypes.c_uint()
        a = ctypes.c_uint()
        if pdfium_c.FPDFText_GetFillColor(
            tp_raw, index, ctypes.byref(r), ctypes.byref(g), ctypes.byref(b), ctypes.byref(a)
        ):
            color = (int(r.value), int(g.value), int(b.value))
    except _STYLE_PROBE_ERRORS:
        color = None

    baseline_y: float | None = None
    try:
        matrix = pdfium_c.FS_MATRIX()
        if pdfium_c.FPDFText_GetMatrix(tp_raw, index, ctypes.byref(matrix)):
            baseline_y = (page_height_pt - matrix.f) * sy
    except _STYLE_PROBE_ERRORS:
        baseline_y = None

    return {
        "font_name": name,
        "font_flags": flags.value or None,
        "font_size_pt": size_pt,
        "font_weight": weight_val,
        "color": color,
        "baseline_y": baseline_y,
    }


def _emit_word(
    boxes: list[WordBox],
    text: str,
    style: dict[str, Any],
    *,
    left: float,
    bottom: float,
    right: float,
    top: float,
    sx: float,
    sy: float,
    page_height_pt: float,
) -> None:
    """Append one word-level :class:`WordBox`, converting pt bbox -> raster px.

    ``style`` is the kwargs dict from :func:`_native_char_style` (empty for a
    textpage with no raw handle), spread onto the box's optional style fields.
    """
    x_px = round(left * sx)
    y_px = round((page_height_pt - top) * sy)
    w_px = max(1, round((right - left) * sx))
    h_px = max(1, round((top - bottom) * sy))
    boxes.append(
        WordBox(text=text, x=x_px, y=y_px, width=w_px, height=h_px, confidence=100.0, **style)
    )


def _walk_chars_into_words(
    tp: object,
    *,
    raster_width_px: int,
    raster_height_px: int,
    page_width_pt: float,
    page_height_pt: float,
) -> list[WordBox]:
    """Walk the textpage's per-char API and group runs into word-level boxes.

    Split out so :func:`extract_native_word_boxes` stays small enough for
    ruff's PLR0915. Caller owns the textpage lifecycle.
    """
    gap_factor = 0.6  # tuned on slide-deck inputs; chars within 0.6 line-height
    # of the previous char are part of the same word.
    sx = raster_width_px / page_width_pt
    sy = raster_height_px / page_height_pt
    tp_raw = getattr(tp, "raw", None)  # ctypes handle for the per-glyph style probe
    n_chars = tp.count_chars()  # type: ignore[attr-defined]
    boxes: list[WordBox] = []
    cur_text: list[str] = []
    cur_left = cur_right = cur_bottom = cur_top = 0.0
    # A word inherits the style of its first glyph (a dict so flush() can mutate,
    # not rebind, it through the closure). Empty -> WordBox style fields default
    # to None, e.g. when the textpage exposes no raw handle.
    cur_style: dict[str, Any] = {}
    prev_baseline = -1.0
    prev_right = -1.0
    prev_height = 0.0

    def flush() -> None:
        if not cur_text:
            return
        text = "".join(cur_text)
        if text.strip():
            _emit_word(
                boxes,
                text,
                cur_style,
                left=cur_left,
                bottom=cur_bottom,
                right=cur_right,
                top=cur_top,
                sx=sx,
                sy=sy,
                page_height_pt=page_height_pt,
            )
        cur_text.clear()
        cur_style.clear()

    for i in range(n_chars):
        ch = tp.get_text_range(i, 1)  # type: ignore[attr-defined]
        if ch.isspace():
            flush()
            prev_baseline = -1.0
            prev_right = -1.0
            continue
        left, bottom, right, top = tp.get_charbox(i)  # type: ignore[attr-defined]
        height = top - bottom
        # Word break on either:
        #   - Line break: baseline jump greater than half the line height, OR
        #   - Wide horizontal gap (column / inter-word break)
        line_break = prev_baseline >= 0 and abs(bottom - prev_baseline) > 0.5 * max(
            prev_height, height
        )
        column_break = prev_right >= 0 and (left - prev_right) > gap_factor * max(
            prev_height, height
        )
        if line_break or column_break:
            flush()
        if not cur_text:
            cur_left, cur_bottom, cur_right, cur_top = left, bottom, right, top
            if tp_raw is not None:
                cur_style.update(
                    _native_char_style(tp_raw, i, page_height_pt=page_height_pt, sy=sy)
                )
        else:
            cur_left = min(cur_left, left)
            cur_right = max(cur_right, right)
            cur_bottom = min(cur_bottom, bottom)
            cur_top = max(cur_top, top)
        cur_text.append(ch)
        prev_baseline = bottom
        prev_right = right
        prev_height = height
    flush()
    return boxes
