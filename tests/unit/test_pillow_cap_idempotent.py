"""Pillow cap installs whether or not pdf_smasher.__init__ ran."""

from __future__ import annotations


def test_ensure_capped_is_idempotent() -> None:
    import PIL.Image

    from pdf_smasher._pillow_hardening import MAX_IMAGE_PIXELS, ensure_capped

    ensure_capped()
    ensure_capped()
    assert PIL.Image.MAX_IMAGE_PIXELS == MAX_IMAGE_PIXELS
