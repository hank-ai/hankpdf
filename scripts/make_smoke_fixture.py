#!/usr/bin/env python3
"""Generate the smoke-test PDFs used by Docker CI.

Writes two fixtures to tests/fixtures/:

- ``smoke.pdf``: a single blank letter-sized page. Used for the
  version/no-op path; idempotent, tiny.
- ``smoke_text.pdf``: a 2-page rendered-text PDF — scanner-style,
  black text on white. This variant forces the MRC pipeline into
  MIXED / TEXT_ONLY so jbig2enc is exercised on the mask/foreground
  path. Used by the docker smoke test to assert non-trivial
  compression (ratio > 2x).

Idempotent — overwrites on every run.
"""

from __future__ import annotations

import io
import pathlib
import sys

import pikepdf
from PIL import Image, ImageDraw, ImageFont

_US_LETTER_PT = (612, 792)
_DPI = 300


def _render_text_page(
    lines: list[str],
    width_pt: float = 612,
    height_pt: float = 792,
    dpi: int = _DPI,
) -> Image.Image:
    """Render a PIL RGB image with black text on white, scanner-style.

    Uses the default Pillow font because any TrueType path requires
    host fonts to be present on every CI image we might ever run on
    (Linux vs macOS differ). The default bitmap font is tiny but
    deterministic and dense — exactly what a TEXT_ONLY classifier
    wants to see.
    """
    w_px = int(width_pt / 72 * dpi)
    h_px = int(height_pt / 72 * dpi)
    img = Image.new("RGB", (w_px, h_px), "white")
    draw = ImageDraw.Draw(img)
    # Use the default bitmap font but tiled through multiple draw calls
    # with a 2-pixel offset to "thicken" the strokes. The default font
    # is too thin and pale at 200 DPI to reliably hit TEXT_ONLY; the
    # overlap trick yields a denser black-pixel area that the mask
    # builder can latch onto.
    try:
        font = ImageFont.load_default()
    except OSError:
        font = None
    y = 72
    line_height = 36
    left = 72
    for line in lines:
        for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
            draw.text((left + dx, y + dy), line, fill="black", font=font)
        y += line_height
    return img


def _image_page_pdf(img: Image.Image, width_pt: float, height_pt: float) -> pikepdf.Pdf:
    """Wrap a rendered image as a 1-page PDF via pikepdf."""
    pdf = pikepdf.new()
    # Save as PNG (lossless) in-memory; pikepdf places it as an image
    # object on the page. We don't use JPEG here because the fixture
    # needs to compress via the MRC pipeline (not already-compressed
    # bytes the pipeline would pass through).
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    xobj = pdf.make_stream(buf.getvalue())
    xobj.ImageMask = False
    # Let pikepdf's attach_image path do the heavy lifting instead —
    # simpler and more portable across pikepdf versions.
    pdf.add_blank_page(page_size=(width_pt, height_pt))
    page = pdf.pages[0]
    # Use pikepdf's helper via a byte stream → place image centered.
    # For fixture purposes we do the simplest thing: rasterize and
    # attach; exact positioning doesn't matter for the smoke test.
    del page  # currently unused — embedded image in a form XObject is
    # overkill for a fixture. Re-do via PIL save-as-PDF:
    final_buf = io.BytesIO()
    img.save(final_buf, format="PDF", resolution=float(_DPI))
    final_buf.seek(0)
    return pikepdf.open(final_buf)


def generate_blank(target: pathlib.Path) -> int:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=_US_LETTER_PT)  # US letter at 72 dpi
    pdf.save(target)
    return target.stat().st_size


def generate_text_heavy(target: pathlib.Path) -> int:
    """Generate a 2-page text-heavy PDF for compression smoke tests.

    Each page is rendered at 300 DPI and embedded into the PDF as an
    UNCOMPRESSED DeviceRGB Image XObject — deliberately fat so the MRC
    pipeline has room to prove itself. A healthy run compresses by
    > 2x because the foreground mask captures most of the inked
    pixels, JBIG2 crushes the mask, and the uniform-white background
    compresses almost to nothing.

    We use pikepdf rather than PIL's save(..., format="PDF") because
    Pillow picks DCT (JPEG) compression by default, which pre-compresses
    the raster to the point that MRC can only squeeze another 1.5-2x
    out of it. An uncompressed input is what a typical scanner
    produces before anyone's touched it.
    """
    # Deterministic content — same bytes every run so CI output
    # comparisons are reproducible.
    lines_per_page = 30
    filler = (
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
        "Sed do eiusmod tempor incididunt ut labore et dolore."
    )

    pdf = pikepdf.new()
    for page_num in range(1, 3):
        lines = [
            f"HankPDF smoke fixture — page {page_num}",
            "",
            "This file is a synthetic scanned-document fixture used by",
            "the Docker CI smoke test to exercise the MRC compression",
            "pipeline end to end. Real scans are not included in the",
            "test corpus for privacy reasons.",
            "",
            "The classifier should route this page through TEXT_ONLY or",
            "MIXED depending on the specific raster statistics on this",
            "host. Either way, the foreground mask + JBIG2 pipeline",
            "should produce an output smaller than the input.",
            "",
        ]
        while len(lines) < lines_per_page:
            lines.append(filler)
        img = _render_text_page(lines)

        w_px, h_px = img.size
        # Build a FlateDecode'd DeviceRGB XObject. pikepdf's
        # make_stream default compresses with flate, which mirrors
        # what most scanners actually write — raw RGB is rarely seen
        # in the wild. The MRC pipeline has ~2x headroom over flate
        # on this fixture; the CI smoke test asserts ratio > 2x.
        raw = img.tobytes()
        xobj = pdf.make_stream(raw)
        xobj.Type = pikepdf.Name("/XObject")
        xobj.Subtype = pikepdf.Name("/Image")
        xobj.Width = w_px
        xobj.Height = h_px
        xobj.BitsPerComponent = 8
        xobj.ColorSpace = pikepdf.Name("/DeviceRGB")

        pdf.add_blank_page(page_size=_US_LETTER_PT)
        page = pdf.pages[-1]
        # Place the image filling the full page.
        w_pt, h_pt = _US_LETTER_PT
        page.Resources = pikepdf.Dictionary(
            XObject=pikepdf.Dictionary(Im0=xobj),
        )
        stream_str = f"q\n{w_pt} 0 0 {h_pt} 0 0 cm\n/Im0 Do\nQ\n"
        page.Contents = pdf.make_stream(stream_str.encode("ascii"))

    buf = io.BytesIO()
    # linearize=False + deterministic_id=True keeps the output bytes
    # stable across repeated fixture regen.
    pdf.save(buf, linearize=False, deterministic_id=True)
    target.write_bytes(buf.getvalue())
    return target.stat().st_size


def main() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    fixtures = root / "tests" / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)

    blank = fixtures / "smoke.pdf"
    text = fixtures / "smoke_text.pdf"

    blank_size = generate_blank(blank)
    text_size = generate_text_heavy(text)

    print(f"wrote {blank} ({blank_size} bytes)")
    print(f"wrote {text} ({text_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
