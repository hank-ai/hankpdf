"""Engine package. Public API is in ``pdf_smasher`` at top level.

Phase 0 scaffold: empty. Phase 2 populates:

- ``triage.py`` — structural scan
- ``sanitize.py`` — stripping + repair
- ``recompress.py`` — MRC pipeline (rasterize, segment, encode, compose)
- ``strategy.py`` — per-page strategy selector
- ``cmyk_prepass.py`` — managed CMYK → sRGB conversion
- ``codecs/`` — JBIG2, JPEG2000, JPEG, CCITT wrappers
"""
