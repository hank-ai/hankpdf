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
