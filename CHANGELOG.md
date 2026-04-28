# Changelog

All notable changes to `pdf-smasher` (HankPDF) are documented here. The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows pre-1.0 SemVer (anything may break between minor versions until 1.0).

## [Unreleased]

### Added
- **Per-page MRC gate (`--per-page-min-image-fraction`, default `0.30`).** Before the pipeline splits work across workers, each page is scored on `image_xobject_bytes / page_byte_budget` (a cheap stream-length signal — no decode, no render). Pages below the threshold are emitted verbatim; pages at or above go through the full MRC pipeline. When no page meets the threshold, the whole-doc passthrough shortcut fires (input bytes returned unchanged, `status="passed_through"`, warning `passthrough-no-image-content`). Native-export and text-only PDFs see ~15× faster wall time (3.83s → 0.25s on a 50-page text-only PDF) and avoid the 53× inflate-and-discard cycle. Image-heavy inputs are unchanged. `--re-ocr`, `--strip-text-layer`, and `--verify` all disable the gate. See `docs/superpowers/specs/2026-04-27-per-page-selective-mrc-design.md` and the new "Per-page MRC gate" section in `docs/PERFORMANCE.md`.
- `CompressReport.pages_skipped_verbatim: tuple[int, ...]` — page indices skipped by the per-page gate. Empty tuple on full-pipeline runs and on whole-doc passthrough.
- `CompressReport.warnings` codes: `passthrough-no-image-content` (whole-doc shortcut) and `pages-skipped-verbatim-N` (partial-run aggregate).
- `CompressOptions.min_image_byte_fraction: float = 0.30` and CLI flag `--per-page-min-image-fraction`.
- New module `pdf_smasher.engine.page_classifier` exposing `score_pages_for_mrc(pdf_bytes, *, password, min_image_byte_fraction) -> list[bool]`.
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
- `docs/PERFORMANCE.md` — measured compression ratios and wall-times across the full settings matrix on three real-world inputs (small/medium/large) plus a synthetic-text scan, with visual quality assessment of representative outputs and per-use-case settings recommendations. Updates the "Honest compression targets" line in the README to call out that the 8-15× typical figure applies to *scanned* inputs, not natively-exported PDFs.
- **Native text-layer preservation as the default** — the MRC pipeline now reads the input PDF's existing text layer (when present) via pdfium and reuses the original text + bounding boxes verbatim in the output, regardless of whether `--ocr` is set. Searchable inputs stay searchable; text is byte-faithful to the source with no Tesseract recognition errors. **`--ocr` semantics changed:** it now means "ensure the output is searchable" — Tesseract runs only on pages where the input has no text OR the existing text fails a quality heuristic (`is_native_text_decent`). The heuristic rejects mostly-symbol noise, the "S c a l i n g" single-char-flood pattern, and gibberish word-length signatures.
- **New `--strip-text-layer` flag** — explicitly remove any text layer in the output. Use for size-only workflows where searchability is unwanted.
- **New `--re-ocr` flag** — force Tesseract on every page even when the input has good native text. Use when an upstream OCR is known-bad and you want a fresh Tesseract pass.
- New `pdf_smasher.engine.text_layer.extract_native_word_boxes` and `is_native_text_decent` helpers; tests in `tests/unit/engine/test_native_text_extraction.py`. **Behavior change for users who relied on the old "no `--ocr` → no text layer" assumption** — those workflows now need `--strip-text-layer` to keep the previous text-free behavior.

### Changed
- **CompressReport schema bumped from v3 → v4.** Additive only (new fields default to empty/zero). Existing v3 readers must not assert `schema_version == 3`. See SPEC.md §11 migration table.
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
