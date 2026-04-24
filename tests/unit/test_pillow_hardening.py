"""Regression: PIL.Image.MAX_IMAGE_PIXELS is set at package import."""

from __future__ import annotations

import PIL.Image


def test_pil_max_matches_image_export_bomb_cap() -> None:
    """Regression: the Pillow cap and the pre-allocation bomb cap must match
    or the two layers of defense protect different things.

    If one drifts (e.g. someone tightens image_export.py to ~500 Mpx and
    forgets _pillow_hardening.py), the Pillow layer would let a 600 Mpx
    image through, the pre-allocation check would catch it — but the
    asymmetric wrong-layer-raises path emits a PIL.Image.DecompressionBomb
    error class instead of our typed exception, routing past the CLI's
    exception-to-exit-code mapping.
    """
    # Trigger pdf_smasher's side-effect import so MAX_IMAGE_PIXELS is set.
    import pdf_smasher  # noqa: F401
    from pdf_smasher.engine.image_export import _MAX_BOMB_PIXELS

    assert PIL.Image.MAX_IMAGE_PIXELS == _MAX_BOMB_PIXELS, (
        f"Pillow cap ({PIL.Image.MAX_IMAGE_PIXELS:,}) and image_export "
        f"bomb cap ({_MAX_BOMB_PIXELS:,}) drifted; a single source of "
        f"truth in pdf_smasher._limits.MAX_BOMB_PIXELS is required."
    )


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
        f"expected MAX_IMAGE_PIXELS={expected:,} (~715 Mpx); got {PIL.Image.MAX_IMAGE_PIXELS!r}"
    )
