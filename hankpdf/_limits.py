"""Centralized numeric limits.

Single source of truth for package-wide size/count caps that appear in
multiple code paths. Without this, a literal like the decompression-bomb
cap drifts between modules (Pillow's MAX_IMAGE_PIXELS vs the
pre-allocation guard in engine.image_export), and the two layers of
defense start protecting different things.
"""

from __future__ import annotations

# ~715 Mpx. Matches Pillow's MAX_IMAGE_PIXELS cap and the image_export
# pre-allocation bomb guard. ~2 GiB of raw RGB pixels (3 bytes each) —
# the maximum unsigned-32-bit allocation we allow anywhere in the
# library, chosen to reject obvious decompression bombs without
# rejecting legitimate large archival scans.
MAX_BOMB_PIXELS: int = 2 * 1024 * 1024 * 1024 // 3

# 200 inches × 72 pt/in = 14400 pt. Hard cap on either MediaBox axis at
# the input-policy gate. Catches PDFs that declare a multi-mile page
# (e.g., 60000 × 20000 pt) before the per-page rasterizer's
# MAX_BOMB_PIXELS guard fires — the rasterizer guard runs inside a
# worker, after the per-page MRC gate has already accepted the document
# for processing, so a page that's empty of image content slips through
# the worker entirely and lands in the verbatim/passthrough fast path.
# This pre-rasterize axis cap closes that gap by refusing at triage time
# regardless of page content density. Real-world arch-D plotter sheets
# top out at 36" × 48"; 200" leaves enormous headroom for legitimate
# engineering drawings while rejecting obvious bomb dimensions.
MAX_PAGE_AXIS_PT: float = 14400.0
