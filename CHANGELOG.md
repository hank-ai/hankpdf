# Changelog

All notable changes to `pdf-smasher` (HankPDF) are documented here. The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows pre-1.0 SemVer (anything may break between minor versions until 1.0).

## [Unreleased]

### Added
- Shared render-size cap helper (`pdf_smasher.engine._render_safety.check_render_size`) used by both the compress (`rasterize.py`) and image-export (`image_export.py`) paths. Closes a decompression-bomb gap on the compress path.
- `--password-file` now plumbs the password through to every PDF-open site that touches user-supplied encrypted bytes (`engine.triage.triage`, public `pdf_smasher.triage`, `engine.canonical.canonical_input_sha256`, the per-page split + page-sizing pdfium open in `compress`, the image-export route's `iter_pages_as_images` chain, and the shared `engine.rasterize.rasterize_page`).
- `_walk_dict_for_names` in triage now fails closed past its depth cap (raises `MaliciousPDFError` instead of silently early-returning); cap raised from 12 to 64 for legitimate-PDF headroom; cycle detection switched from Python `id()` to pikepdf's `objgen` so the visited-set actually dedupes indirect refs.
- POSIX `O_NOFOLLOW` on the partial-write path in `pdf_smasher.utils.atomic._atomic_write_bytes`. A pre-placed symlink at the partial path is now refused. Windows path is unchanged (no `O_NOFOLLOW` equivalent without ctypes).
- Idempotent `pdf_smasher._pillow_hardening.ensure_capped()`. Engine modules that import PIL now self-install the cap so programmatic callers using only an engine submodule still get the protection.
- Native binary paths (`jbig2`, `tesseract`, `qpdf`) resolved to absolute paths once via cached `shutil.which`.
- `.github/workflows/release.yml` — dormant PyPI release workflow with OIDC trusted publishing. Triggered only by published GitHub Releases. No `PYPI_API_TOKEN` secret is introduced. Configure the publisher entry on pypi.org once before cutting the first release.
- `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1).
- `.github/ISSUE_TEMPLATE/{bug_report,feature_request,config}.yml` and `.github/PULL_REQUEST_TEMPLATE.md`.
- `pre-commit` ecosystem in `.github/dependabot.yml`.
- `docs/ARCHITECTURE.md` — new "Render-size protection" section documenting the two-tier cap (`_render_safety.check_render_size` pre-allocation + Pillow `MAX_IMAGE_PIXELS` post-decode).

### Changed
- **BREAKING (CLI):** `--max-input-mb` default lowered from `2000.0` to `250.0`. To restore previous behavior: `--max-input-mb 2000`.
- **BREAKING (CLI):** `--max-pages` default lowered from "unlimited" to `10000`. To restore previous behavior: `--max-pages 100000` (or higher).
- **Library API note:** `CompressOptions.max_input_mb` default also tightened to `250.0`. `CompressOptions.max_pages` default tightened from `None` to `10000`; the type stays `int | None`, so programmatic callers can still pass `max_pages=None` to opt into the previous unlimited behavior.
- CLI `--password-file` read switched from locale-default decoding + `.strip()` to UTF-8 decoding with targeted CR/LF/CRLF stripping. Passwords with internal whitespace are now preserved; Windows-line-ending password files now work.
- Refusal messages for both `max_input_mb` and `max_pages` now include the override flag so users hitting the new caps know how to relax them.
- `TriageReport.is_encrypted` now reflects the actual encryption status of the input even after a successful password-decrypt (was always `False` on the success path; now propagated from `pdf.is_encrypted`).

### Security
- New POSIX `O_NOFOLLOW` defense (see Added).
- Triage depth-cap walker now fails closed (see Added).
- Decompression-bomb pre-allocation cap now applied on the compress path (previously only the image-export path).

### Repository
- Replaced placeholder `ourorg/pdf-smasher` URLs with the real `hank-ai/hankpdf` URLs across `pyproject.toml`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`.
- Removed placeholder `security@TBD.example` from `SECURITY.md`. GitHub Security Advisories is now the sole reporting channel.
- Storage-agnostic corpus mirror story (`s3_mirror` field renamed to `mirror_url`; docs no longer assume S3).
- README test count and Docker-image tag examples updated to reflect reality.
