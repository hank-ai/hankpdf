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


# ---------- Task 4b: force_monochrome ----------


@pytest.mark.integration
def test_force_monochrome_applies_to_photo_only_pages_too() -> None:
    """A page classified PHOTO_ONLY must encode as grayscale when force_monochrome=True."""
    import numpy as np

    arr = np.zeros((2550, 3300, 3), dtype=np.uint8)
    arr[..., 0] = np.linspace(100, 255, 3300, dtype=np.uint8)[None, :]
    arr[..., 2] = np.linspace(255, 100, 3300, dtype=np.uint8)[None, :]
    img = Image.fromarray(arr)
    pdf_in = _wrap_raster_as_pdf_bytes(img)

    from pdf_smasher import CompressOptions

    _, default_report = compress(pdf_in)
    _, mono_report = compress(pdf_in, options=CompressOptions(force_monochrome=True))

    assert mono_report.output_bytes < default_report.output_bytes * 0.85, (
        f"force_monochrome must reduce photo-only size: "
        f"default={default_report.output_bytes}, mono={mono_report.output_bytes}"
    )


@pytest.mark.integration
def test_force_monochrome_emits_color_warning_on_colored_page() -> None:
    """SPEC.md:402 — `page-N-color-detected-in-monochrome-mode` emitted when colored content is flattened."""
    import numpy as np

    arr = np.full((1700, 2200, 3), 240, dtype=np.uint8)
    arr[400:500, 400:1200] = [200, 40, 40]  # big red stamp
    img = Image.fromarray(arr)
    pdf_in = _wrap_raster_as_pdf_bytes(img, page_width_pt=612, page_height_pt=792)

    from pdf_smasher import CompressOptions

    _, report = compress(pdf_in, options=CompressOptions(force_monochrome=True))
    assert any("color-detected-in-monochrome-mode" in w for w in report.warnings), (
        f"expected color warning; got warnings={report.warnings}"
    )


# ---------- Task 4c: colored stamp + multi-page ----------


@pytest.mark.integration
def test_colored_stamp_is_preserved_after_compression() -> None:
    """Pre-Mortem #1: a page with a red stamp must NOT be silently flattened.

    The strategy chosen is an implementation detail — what matters is the
    verifier catches silent color loss and the test asserts color at the
    stamp location in the output.
    """
    import numpy as np
    import pypdfium2 as pdfium

    arr = np.full((2550, 3300, 3), 255, dtype=np.uint8)
    arr[500:600, 500:2000] = 0           # black text band (~1% area)
    arr[1500:1700, 1500:2500] = [200, 40, 40]  # red stamp (~2% area)
    img = Image.fromarray(arr)
    pdf_in = _wrap_raster_as_pdf_bytes(img)

    pdf_out, report = compress(pdf_in)
    assert report.status == "ok"

    doc = pdfium.PdfDocument(pdf_out)
    try:
        rendered = doc[0].render(scale=100 / 72).to_pil().convert("RGB")
    finally:
        doc.close()
    out_arr = np.asarray(rendered, dtype=np.int16)
    yh, xh = out_arr.shape[:2]
    # arr shape (2550, 3300, 3) → image width=3300, height=2550
    # stamp center: row=1600/2550 of height, col=2000/3300 of width
    stamp_y = int(1600 / 2550 * yh)
    stamp_x = int(2000 / 3300 * xh)
    patch = out_arr[stamp_y - 20 : stamp_y + 20, stamp_x - 20 : stamp_x + 20]
    channel_spread = patch.max(axis=-1) - patch.min(axis=-1)
    assert channel_spread.max() > 30, (
        f"red stamp lost through compression; max channel spread = "
        f"{channel_spread.max()} at {(stamp_y, stamp_x)}"
    )


@pytest.mark.integration
def test_multi_page_mixed_strategies_merges_correctly() -> None:
    """3-page PDF where each page hits a different strategy — all must survive."""
    import io as _io

    import numpy as np
    import pikepdf

    from pdf_smasher.engine.mask import build_mask
    from pdf_smasher.engine.rasterize import rasterize_page
    from pdf_smasher.engine.strategy import PageStrategy, classify_page

    def _text_only_raster() -> Image.Image:
        img = Image.new("RGB", (2550, 3300), "white")
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("Arial.ttf", 72)
        except OSError:
            font = ImageFont.load_default(size=72)
        y, r = 80, 0
        while y < 3200:
            draw.text((100, y), f"TEXT ONLY LINE {r}: ICD-10 A00.{r % 100:02d} dose 1.25mg", fill="black", font=font)
            y += 90
            r += 1
        return img

    def _photo_only_raster() -> Image.Image:
        # All pixels bright (≥180) — no dark "ink" pixels, so mask_coverage ≈ 0 → PHOTO_ONLY
        arr = np.full((2550, 3300, 3), 200, dtype=np.uint8)
        arr[..., 0] = np.linspace(180, 255, 3300, dtype=np.uint8)[None, :]
        arr[..., 2] = np.linspace(255, 180, 3300, dtype=np.uint8)[None, :]
        return Image.fromarray(arr)

    def _mixed_raster() -> Image.Image:
        # Solid medium-gray bg (no noise — noise causes garbage OCR) + a large
        # dark rectangle to push mask_coverage > 5% and light_frac < 80%, which
        # is what the classifier requires for MIXED.
        img = Image.new("RGB", (2550, 3300), color=(150, 150, 150))
        draw = ImageDraw.Draw(img)
        # Dark ink band covering ~15% of the page — guarantees MIXED routing.
        draw.rectangle((0, 1500, 2550, 2000), fill=(20, 20, 20))
        try:
            import pathlib as _pl
            _fp = _pl.Path("tests/integration/_fixtures/LiberationMono-Regular.ttf")
            font = ImageFont.truetype(str(_fp), 96) if _fp.exists() else ImageFont.truetype("LiberationMono-Regular.ttf", 96)
        except OSError:
            font = ImageFont.load_default(size=96)
        for r in range(8):
            draw.text((200, 200 + r * 140), f"MIXED LINE {r:02d}", fill="black", font=font)
        return img

    pdf = pikepdf.new()
    for raster_fn in (_text_only_raster, _photo_only_raster, _mixed_raster):
        img = raster_fn()
        pdf.add_blank_page(page_size=(612, 792))
        page = pdf.pages[-1]
        jpeg = _io.BytesIO()
        img.save(jpeg, format="JPEG", quality=95, subsampling=0)
        xobj = pdf.make_stream(
            jpeg.getvalue(),
            Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
            Width=img.size[0], Height=img.size[1],
            ColorSpace=pikepdf.Name.DeviceRGB, BitsPerComponent=8,
            Filter=pikepdf.Name.DCTDecode,
        )
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Scan=xobj))
        page.Contents = pdf.make_stream(b"q 612 0 0 792 0 0 cm /Scan Do Q\n")

    buf = _io.BytesIO()
    pdf.save(buf)
    pdf_in = buf.getvalue()

    def _route(page_idx: int) -> PageStrategy:
        raster = rasterize_page(pdf_in, page_index=page_idx, dpi=150)
        mask_arr = np.asarray(build_mask(raster).convert("1"), dtype=bool)
        cov = float(mask_arr.sum()) / max(1, mask_arr.size)
        return classify_page(raster, mask_coverage_fraction=cov)

    assert _route(0) == PageStrategy.TEXT_ONLY, "Page 0 must be TEXT_ONLY"
    assert _route(1) == PageStrategy.PHOTO_ONLY, "Page 1 must be PHOTO_ONLY"
    assert _route(2) == PageStrategy.MIXED, "Page 2 must be MIXED"

    # fast mode: skip the MRC tile-SSIM gate. Synthetic fixtures with solid
    # rectangles produce legitimately low tile SSIM (crisp JBIG2 vs. anti-aliased
    # JPEG edges); the gate is meant for realistic scanner output, not synthetic
    # high-contrast shapes. This test is about routing + merging, not quality.
    from pdf_smasher import CompressOptions

    _, report = compress(pdf_in, options=CompressOptions(mode="fast"))
    assert report.pages == 3
    assert report.status == "ok"
    assert report.strategy_distribution["text_only"] == 1
    assert report.strategy_distribution["photo_only"] == 1
    assert report.strategy_distribution["mixed"] == 1
