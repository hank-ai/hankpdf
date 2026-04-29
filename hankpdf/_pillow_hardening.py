"""Pillow decompression-bomb cap hardening.

Imported for side effect from :mod:`hankpdf`. SECURITY.md and
``docs/THREAT_MODEL.md`` advertise that ``PIL.Image.MAX_IMAGE_PIXELS`` is
set explicitly so Pillow's :class:`PIL.Image.DecompressionBombError` fires
at a documented threshold rather than the library default (~89 Mpx, which
would leave huge untested inputs accepted silently).

The cap matches :data:`hankpdf.engine.image_export._MAX_BOMB_PIXELS`
(~715 Mpx) so the library-level pre-allocation check and Pillow's internal
check refuse the same inputs.

Translation from :class:`PIL.Image.DecompressionBombError` to our typed
:class:`hankpdf.exceptions.DecompressionBombError` happens at the
call sites (engine.image_export and anywhere else Pillow decodes); this
module only installs the cap.
"""

from __future__ import annotations

import PIL.Image

from hankpdf._limits import MAX_BOMB_PIXELS

# ~715 Mpx. Imported from hankpdf._limits — SINGLE source of truth
# shared with hankpdf.engine.image_export._MAX_BOMB_PIXELS. The
# test in tests/unit/test_pillow_hardening.py asserts both point to
# the same value so a drift can't silently bypass one of the two
# layers of defense.
MAX_IMAGE_PIXELS: int = MAX_BOMB_PIXELS


def ensure_capped() -> None:
    """Idempotent installer of Pillow's decompression-bomb cap.

    Engine modules call this at module-load time so a programmatic
    caller that imports only ``hankpdf.engine.<x>`` (without going
    through ``hankpdf.__init__``) still gets the cap installed.
    Calling more than once is a no-op.
    """
    if PIL.Image.MAX_IMAGE_PIXELS != MAX_IMAGE_PIXELS:
        PIL.Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


# Side-effect call kept so `import hankpdf` still installs the cap;
# call sites that import only an engine submodule call ensure_capped()
# themselves at module top.
ensure_capped()
