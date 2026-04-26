"""Add an invisible OCR text layer to an existing PDF page.

Uses the standard Helvetica Type 1 font (one of the 14 standard fonts every
PDF reader ships) so we don't embed font data. Text is painted with
rendering mode 3 (invisible) so it's searchable and selectable but has no
visual effect.

Coordinates: OCR word boxes are in *raster pixels* (top-left origin, Y
down). PDF text coordinates are in *points* (bottom-left origin, Y up).
We convert one to the other.

Source of word boxes:

- :func:`pdf_smasher.engine.ocr.tesseract_word_boxes` — re-OCR the raster
  via Tesseract. Used for true scans (no upstream text layer).
- :func:`extract_native_word_boxes` (this module) — read text + bboxes
  from the input PDF's existing content stream via pdfium. Faithful and
  Tesseract-free for inputs that already have a searchable text layer.
"""

from __future__ import annotations

import io
from collections.abc import Sequence

import pikepdf
import pypdfium2 as pdfium

from pdf_smasher.engine.ocr import WordBox

_HELVETICA_FONT_NAME = pikepdf.Name("/F-HankPDF-Invisible")


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
    n_chars = tp.count_chars()  # type: ignore[attr-defined]
    boxes: list[WordBox] = []
    cur_text: list[str] = []
    cur_left = cur_right = cur_bottom = cur_top = 0.0
    prev_baseline = -1.0
    prev_right = -1.0
    prev_height = 0.0

    def flush() -> None:
        if not cur_text:
            return
        text = "".join(cur_text)
        if not text.strip():
            cur_text.clear()
            return
        x_px = round(cur_left * sx)
        y_px = round((page_height_pt - cur_top) * sy)
        w_px = max(1, round((cur_right - cur_left) * sx))
        h_px = max(1, round((cur_top - cur_bottom) * sy))
        boxes.append(WordBox(text=text, x=x_px, y=y_px, width=w_px, height=h_px, confidence=100.0))
        cur_text.clear()

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
