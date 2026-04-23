"""Phase-2b ratio gate: assert compression ratios on canonical fixtures.

All tests are marked @pytest.mark.integration and may be skipped if jbig2enc
is unavailable (text-only JBIG2 path requires it for the 20× gate).
"""

from __future__ import annotations

import io

import pikepdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from pdf_smasher import compress


def _wrap_raster_as_pdf_bytes(
    img: Image.Image,
    *,
    page_width_pt: float = 612.0,
    page_height_pt: float = 792.0,
) -> bytes:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(page_width_pt, page_height_pt))
    page = pdf.pages[0]
    jpeg = io.BytesIO()
    img.save(jpeg, format="JPEG", quality=95, subsampling=0)
    xobj = pdf.make_stream(
        jpeg.getvalue(),
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=img.size[0],
        Height=img.size[1],
        ColorSpace=pikepdf.Name.DeviceRGB,
        BitsPerComponent=8,
        Filter=pikepdf.Name.DCTDecode,
    )
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Scan=xobj))
    page.Contents = pdf.make_stream(
        f"q {page_width_pt} 0 0 {page_height_pt} 0 0 cm /Scan Do Q\n".encode("ascii"),
    )
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def _text_only_fixture() -> bytes:
    """8.5x11 inch @ 300 DPI, black text on white — typical medical/legal doc.

    Full-page dense text so mask_coverage > 5% (TEXT_ONLY threshold) at all DPIs.
    Uses size=72 so characters are large enough to survive JPEG rounding + rasterization.
    """
    img = Image.new("RGB", (2550, 3300), color="white")
    draw = ImageDraw.Draw(img)
    _FIXTURE_FONT = "tests/integration/_fixtures/LiberationMono-Regular.ttf"
    try:
        import pathlib as _pl

        _font_path = _pl.Path(_FIXTURE_FONT)
        if _font_path.exists():
            font = ImageFont.truetype(str(_font_path), 72)
        else:
            font = ImageFont.truetype("LiberationMono-Regular.ttf", 72)
    except OSError:
        font = ImageFont.load_default(size=72)
    y = 80
    line_height = 90
    i = 0
    while y < 3200:
        draw.text(
            (100, y),
            f"Line {i + 1:02d}: diagnosis code ICD-10 A00.{i % 100:02d}  dose 1.25mg  amount ${664 + i:.2f}",
            fill="black",
            font=font,
        )
        y += line_height
        i += 1
    return _wrap_raster_as_pdf_bytes(img)


@pytest.mark.integration
@pytest.mark.skipif(
    __import__("shutil").which("jbig2") is None,
    reason="jbig2enc not installed — text-only ratio falls to ~8× on flate fallback, test requires ≥20×",
)
def test_text_only_page_hits_target_ratio() -> None:
    """Text-only routing: black text on white should hit >=20x compression.

    Pre-assert checks that _text_only_fixture() actually routes to TEXT_ONLY
    so the ratio assertion is meaningful.
    """
    import numpy as np

    from pdf_smasher.engine.mask import build_mask
    from pdf_smasher.engine.rasterize import rasterize_page
    from pdf_smasher.engine.strategy import PageStrategy, classify_page

    pdf_in = _text_only_fixture()
    _raster = rasterize_page(pdf_in, page_index=0, dpi=150)
    _mask = build_mask(_raster)
    _mask_arr = np.asarray(_mask.convert("1"), dtype=bool)
    _coverage = float(_mask_arr.sum()) / max(1, _mask_arr.size)
    _strategy = classify_page(_raster, mask_coverage_fraction=_coverage)
    assert _strategy == PageStrategy.TEXT_ONLY, (
        f"_text_only_fixture() routed to {_strategy!r} instead of TEXT_ONLY. "
        "Fix the fixture (e.g., anti-aliasing adding color pixels above the "
        "monochrome threshold) before the ratio assertion is meaningful."
    )

    pdf_out, report = compress(pdf_in)
    assert report.ratio >= 20.0, (
        f"text-only fixture should compress >=20x; got {report.ratio:.2f}x "
        f"(in={report.input_bytes:,} out={report.output_bytes:,})"
    )
