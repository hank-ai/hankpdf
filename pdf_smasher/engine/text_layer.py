"""Add an invisible OCR text layer to an existing PDF page.

Uses the standard Helvetica Type 1 font (one of the 14 standard fonts every
PDF reader ships) so we don't embed font data. Text is painted with
rendering mode 3 (invisible) so it's searchable and selectable but has no
visual effect.

Coordinates: OCR word boxes are in *raster pixels* (top-left origin, Y
down). PDF text coordinates are in *points* (bottom-left origin, Y up).
We convert one to the other.
"""

from __future__ import annotations

import io
from collections.abc import Sequence

import pikepdf

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
