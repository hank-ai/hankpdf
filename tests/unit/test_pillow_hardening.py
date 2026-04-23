"""Regression: PIL.Image.MAX_IMAGE_PIXELS is set at package import."""

from __future__ import annotations

import PIL.Image


def test_pil_max_image_pixels_is_set_on_import() -> None:
    """SECURITY.md / THREAT_MODEL.md advertise that the decompression-bomb
    cap is set explicitly. Prove it.

    Pillow's default (~89 Mpx) would silently raise PIL.Image.DecompressionBombError
    — a distinct class from pdf_smasher.DecompressionBombError — which the CLI
    wouldn't route to EXIT_DECOMPRESSION_BOMB. Our hardening caps at the same
    ~715 Mpx number the image_export pre-allocation check uses.
    """
    import pdf_smasher  # noqa: F401 — import side effect sets the cap

    # Must not be None (unlimited) and must be set deliberately.
    assert PIL.Image.MAX_IMAGE_PIXELS is not None
    # Must match our stated budget (belt-and-suspenders with image_export's
    # _MAX_BOMB_PIXELS). Keep this exact so docs stay accurate.
    expected = 2 * 1024 * 1024 * 1024 // 3  # ~715 Mpx, matches _MAX_BOMB_PIXELS
    assert expected == PIL.Image.MAX_IMAGE_PIXELS, (
        f"expected MAX_IMAGE_PIXELS={expected:,} (~715 Mpx); "
        f"got {PIL.Image.MAX_IMAGE_PIXELS!r}"
    )
