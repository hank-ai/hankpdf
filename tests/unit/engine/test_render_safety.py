"""Render-size cap shared by rasterize.py and image_export.py."""

from __future__ import annotations

import pytest

from hankpdf import DecompressionBombError
from hankpdf.engine._render_safety import check_render_size


def test_normal_letter_size_at_300_dpi_passes() -> None:
    # 8.5x11 inches at 300 DPI = 2550x3300 = ~8.4 Mpx, well under cap
    check_render_size(width_pt=612.0, height_pt=792.0, dpi=300.0)


def test_huge_mediabox_at_300_dpi_refuses() -> None:
    with pytest.raises(DecompressionBombError):
        check_render_size(width_pt=1_000_000.0, height_pt=1_000_000.0, dpi=300.0)


def test_modest_dpi_on_small_page_passes() -> None:
    check_render_size(width_pt=612.0, height_pt=792.0, dpi=72.0)


def test_zero_or_negative_dimensions_refuse_with_value_error() -> None:
    with pytest.raises(ValueError, match="invalid page size"):
        check_render_size(width_pt=0.0, height_pt=792.0, dpi=300.0)
    with pytest.raises(ValueError, match="invalid page size"):
        check_render_size(width_pt=-1.0, height_pt=792.0, dpi=300.0)


def test_max_pixels_override_lets_callers_opt_in_to_higher_cap() -> None:
    # Default cap refuses 30000x30000 = 900 Mpx, but caller passes 2 Gpx ceiling.
    check_render_size(
        width_pt=72.0 * 1000.0,
        height_pt=72.0 * 1000.0,
        dpi=30.0,
        max_pixels=2_000_000_000,
    )
