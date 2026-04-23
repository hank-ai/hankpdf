"""Pillow decompression-bomb cap hardening.

Imported for side effect from :mod:`pdf_smasher`. SECURITY.md and
``docs/THREAT_MODEL.md`` advertise that ``PIL.Image.MAX_IMAGE_PIXELS`` is
set explicitly so Pillow's :class:`PIL.Image.DecompressionBombError` fires
at a documented threshold rather than the library default (~89 Mpx, which
would leave huge untested inputs accepted silently).

The cap matches :data:`pdf_smasher.engine.image_export._MAX_BOMB_PIXELS`
(~715 Mpx) so the library-level pre-allocation check and Pillow's internal
check refuse the same inputs.

Translation from :class:`PIL.Image.DecompressionBombError` to our typed
:class:`pdf_smasher.exceptions.DecompressionBombError` happens at the
call sites (engine.image_export and anywhere else Pillow decodes); this
module only installs the cap.
"""

from __future__ import annotations

import PIL.Image

# ~715 Mpx. Keep exact — the test in tests/unit/test_pillow_hardening.py
# asserts the precise value, and the reviewer flagged "set deliberately,
# not Pillow's default" as the security invariant. Matches
# pdf_smasher.engine.image_export._MAX_BOMB_PIXELS.
MAX_IMAGE_PIXELS: int = 2 * 1024 * 1024 * 1024 // 3

PIL.Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
