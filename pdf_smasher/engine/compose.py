"""Compose PDF pages from MRC components.

Three composition modes matching :class:`pdf_smasher.engine.strategy.PageStrategy`:

- :func:`compose_mrc_page` — **mixed** pages. Background JPEG + 1-bit mask
  drawn as ``/ImageMask`` in the ink color. The mask uses JBIG2 compression
  (with a flate fallback). No separate foreground image XObject — the
  ``/ImageMask`` construct paints the mask shape directly in the current
  fill color, which is dramatically smaller than embedding a full-page
  solid-color JPEG with an SMask.
- :func:`compose_text_only_page` — **text-only** pages. Single full-page
  JBIG2 image with ``/ImageMask true``, painted in the ink color over the
  detected paper color as the solid page background. No bg image XObject,
  no inpainting, no JPEG. Typical ratios 20-100x on printed text.
- :func:`compose_photo_only_page` — **photo-only** pages. Single JPEG image
  covering the full page. No mask, no foreground layer.
"""

from __future__ import annotations

import io
import subprocess
import zlib
from typing import Any, Literal

import pikepdf
from PIL import Image

from pdf_smasher._pillow_hardening import ensure_capped
from pdf_smasher.engine.codecs.jbig2 import encode_1bit_jbig2

ensure_capped()

BgColorMode = Literal["rgb", "grayscale"]
BgCodec = Literal["jpeg", "jpeg2000"]

_JPEG_QUALITY_BG = 45
_JPEG_SUBSAMPLING_444 = 0  # 4:4:4 — no chroma subsampling

# Calibrated against JPEG q=45 on synthetic paper texture (600x600 240-bg + ±15
# noise): JPEG q=45 → ~16.8 KB; quality_layers=[80] (Pillow rate mode = X:1
# compression ratio) → ~13.5 KB, ~20% smaller at visually-equivalent quality.
# Re-calibrate via scripts/measure_ratios.py when the bg encode pipeline changes.
_JPEG2000_QUALITY_LAYERS_DEFAULT = [80]


def _jpeg_bytes(
    image: Image.Image,
    quality: int,
    *,
    subsampling: int = _JPEG_SUBSAMPLING_444,
) -> bytes:
    buf = io.BytesIO()
    image.save(
        buf,
        format="JPEG",
        quality=quality,
        subsampling=subsampling,
        optimize=True,
    )
    return buf.getvalue()


def _jpeg2000_bytes(
    image: Image.Image,
    *,
    quality_layers: list[int] | None = None,
) -> bytes | None:
    """Encode as JPEG2000 via Pillow's bundled OpenJPEG. Returns None if the
    encoder is unavailable (Pillow-SIMD / minimal Alpine wheels); callers
    must fall back to JPEG in that case.
    """
    buf = io.BytesIO()
    try:
        image.save(
            buf,
            format="JPEG2000",
            quality_mode="rates",
            quality_layers=quality_layers or _JPEG2000_QUALITY_LAYERS_DEFAULT,
        )
    except OSError, KeyError, TypeError:
        return None
    return buf.getvalue()


def _encode_bg(
    image: Image.Image,
    *,
    bg_codec: BgCodec,
    jpeg_quality: int,
    subsampling: int = _JPEG_SUBSAMPLING_444,
) -> tuple[bytes, Any]:
    """Return (data, pikepdf /Filter name) for the background layer.
    Honors bg_codec='jpeg2000' but falls back to JPEG when OpenJPEG is
    unavailable in the current Pillow build.
    """
    if bg_codec == "jpeg2000":
        data = _jpeg2000_bytes(image)
        if data is not None:
            return data, pikepdf.Name.JPXDecode
    return _jpeg_bytes(image, jpeg_quality, subsampling=subsampling), pikepdf.Name.DCTDecode


def _pack_1bit_msb(mask: Image.Image) -> bytes:
    """Return mask bytes in PDF-expected MSB-first 1-bit-per-pixel layout."""
    m = mask.convert("1")
    return m.tobytes()  # PIL stores 1-bit images MSB-first, padded to byte per row


def _make_stream(pdf: pikepdf.Pdf, **kwargs: Any) -> pikepdf.Stream:
    data = kwargs.pop("data")
    return pdf.make_stream(data, **kwargs)


def _encode_mask_xobject(
    pdf: pikepdf.Pdf,
    mask: Image.Image,
    *,
    as_image_mask: bool,
) -> pikepdf.Stream:
    """Encode a 1-bit mask as a PDF Image XObject.

    ``as_image_mask=True`` sets ``/ImageMask true`` — the mask is painted in
    the current fill color (no color space needed). ``False`` builds a
    regular 1-bit grayscale image suitable for use as an ``/SMask``.
    """
    try:
        data = encode_1bit_jbig2(mask)
        kwargs: dict[str, Any] = {
            "Type": pikepdf.Name.XObject,
            "Subtype": pikepdf.Name.Image,
            "Width": mask.size[0],
            "Height": mask.size[1],
            "BitsPerComponent": 1,
            "Filter": pikepdf.Name.JBIG2Decode,
        }
    except FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired:
        data = zlib.compress(_pack_1bit_msb(mask), level=9)
        kwargs = {
            "Type": pikepdf.Name.XObject,
            "Subtype": pikepdf.Name.Image,
            "Width": mask.size[0],
            "Height": mask.size[1],
            "BitsPerComponent": 1,
            "Filter": pikepdf.Name.FlateDecode,
        }
    if as_image_mask:
        kwargs["ImageMask"] = True
        kwargs["Decode"] = pikepdf.Array([1, 0])  # invert: mask bit 1 = painted
    else:
        kwargs["ColorSpace"] = pikepdf.Name.DeviceGray
        kwargs["Decode"] = pikepdf.Array([0, 1])
    return _make_stream(pdf, data=data, **kwargs)


def _new_page_pdf(width_pt: float, height_pt: float) -> tuple[pikepdf.Pdf, Any]:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(width_pt, height_pt))
    return pdf, pdf.pages[0]


def _save(pdf: pikepdf.Pdf) -> bytes:
    out = io.BytesIO()
    pdf.save(out, linearize=False, deterministic_id=True)
    return out.getvalue()


def compose_mrc_page(
    *,
    foreground: Image.Image,  # noqa: ARG001 — reserved for Phase-2 per-region color
    foreground_color: tuple[int, int, int],
    mask: Image.Image,
    background: Image.Image,
    page_width_pt: float,
    page_height_pt: float,
    bg_jpeg_quality: int = _JPEG_QUALITY_BG,
    bg_color_mode: BgColorMode = "rgb",
    bg_codec: BgCodec = "jpeg",
    bg_subsampling: int = _JPEG_SUBSAMPLING_444,
) -> bytes:
    """Build a mixed-content (MRC) PDF page: background + masked foreground."""
    pdf, page = _new_page_pdf(page_width_pt, page_height_pt)

    # --- Background image XObject (JPEG or JPEG2000) ---
    if bg_color_mode == "grayscale":
        bg_prepared = background.convert("L")
        bg_color_space = pikepdf.Name.DeviceGray
    else:
        bg_prepared = background.convert("RGB")
        bg_color_space = pikepdf.Name.DeviceRGB
    bg_data, bg_filter = _encode_bg(
        bg_prepared,
        bg_codec=bg_codec,
        jpeg_quality=bg_jpeg_quality,
        subsampling=bg_subsampling,
    )
    bg_xobj = _make_stream(
        pdf,
        data=bg_data,
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=bg_prepared.size[0],
        Height=bg_prepared.size[1],
        ColorSpace=bg_color_space,
        BitsPerComponent=8,
        Filter=bg_filter,
    )

    # --- Mask XObject painted as /ImageMask — no separate foreground image ---
    mask_xobj = _encode_mask_xobject(pdf, mask, as_image_mask=True)

    page.Resources = pikepdf.Dictionary(
        XObject=pikepdf.Dictionary(BG=bg_xobj, MASK=mask_xobj),
    )

    # PDF content stream: paint background full-page, then set ink color and
    # paint the mask in that color (image mask → current fill).
    r, g, b = (c / 255.0 for c in foreground_color)
    ops = (
        f"q {page_width_pt} 0 0 {page_height_pt} 0 0 cm /BG Do Q\n"
        f"q {r:.4f} {g:.4f} {b:.4f} rg\n"
        f"{page_width_pt} 0 0 {page_height_pt} 0 0 cm /MASK Do Q\n"
    ).encode("ascii")
    page.Contents = pdf.make_stream(ops)
    return _save(pdf)


def compose_text_only_page(
    *,
    mask: Image.Image,
    foreground_color: tuple[int, int, int],
    paper_color: tuple[int, int, int] = (255, 255, 255),
    page_width_pt: float,
    page_height_pt: float,
) -> bytes:
    """Text-only page: solid paper color + JBIG2 ink mask. No bg image XObject."""
    pdf, page = _new_page_pdf(page_width_pt, page_height_pt)
    mask_xobj = _encode_mask_xobject(pdf, mask, as_image_mask=True)
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(MASK=mask_xobj))

    pr, pg, pb = (c / 255.0 for c in paper_color)
    ir, ig, ib = (c / 255.0 for c in foreground_color)
    ops = (
        # Paper: filled rectangle over the full page in paper color.
        f"q {pr:.4f} {pg:.4f} {pb:.4f} rg\n"
        f"0 0 {page_width_pt} {page_height_pt} re f Q\n"
        # Ink: mask image painted in foreground color.
        f"q {ir:.4f} {ig:.4f} {ib:.4f} rg\n"
        f"{page_width_pt} 0 0 {page_height_pt} 0 0 cm /MASK Do Q\n"
    ).encode("ascii")
    page.Contents = pdf.make_stream(ops)
    return _save(pdf)


def compose_photo_only_page(
    *,
    raster: Image.Image,
    page_width_pt: float,
    page_height_pt: float,
    target_dpi: int,
    jpeg_quality: int = _JPEG_QUALITY_BG,
    subsampling: int = _JPEG_SUBSAMPLING_444,
    bg_color_mode: BgColorMode = "rgb",
    bg_codec: BgCodec = "jpeg",
) -> bytes:
    """Photo-only page: single full-page JPEG or JPEG2000. No mask."""
    pdf, page = _new_page_pdf(page_width_pt, page_height_pt)

    target_w = max(1, round(page_width_pt * target_dpi / 72))
    target_h = max(1, round(page_height_pt * target_dpi / 72))
    if bg_color_mode == "grayscale":
        resized = raster.convert("L").resize((target_w, target_h), Image.Resampling.LANCZOS)
        color_space = pikepdf.Name.DeviceGray
    else:
        resized = raster.convert("RGB").resize((target_w, target_h), Image.Resampling.LANCZOS)
        color_space = pikepdf.Name.DeviceRGB
    data, filter_name = _encode_bg(
        resized,
        bg_codec=bg_codec,
        jpeg_quality=jpeg_quality,
        subsampling=subsampling,
    )
    xobj = _make_stream(
        pdf,
        data=data,
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=resized.size[0],
        Height=resized.size[1],
        ColorSpace=color_space,
        BitsPerComponent=8,
        Filter=filter_name,
    )
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(IM=xobj))
    page.Contents = pdf.make_stream(
        f"q {page_width_pt} 0 0 {page_height_pt} 0 0 cm /IM Do Q\n".encode("ascii"),
    )
    return _save(pdf)
