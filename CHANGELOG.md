# Changelog

All notable changes to `hankpdf` (formerly `pdf-smasher` on PyPI) are documented here. The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows pre-1.0 SemVer (anything may break between minor versions until 1.0).

## [Unreleased]

## [0.3.0] - 2026-05-03

### Removed (BREAKING)

- `pdf_smasher/` deprecation shim. The shim was a one-cycle soft landing
  for the v0.2.0 distribution rename. Migrate now:
  - Was: `from pdf_smasher import compress`
  - Now: `from hankpdf import compress`
  - Was: `pdf_smasher.__version__`
  - Now: `hankpdf.__version__`
  - Was: `importlib.metadata.version("pdf-smasher")`
  - Now: `importlib.metadata.version("hankpdf")`
  Run your test suite under
  `PYTHONWARNINGS=error::DeprecationWarning` to catch any remaining
  shim usage before upgrading.

### Added

- **Native-dep boot check.** `hankpdf` now fails loud at startup with
  a per-platform install hint when Tesseract / qpdf / OpenJPEG /
  jbig2enc are missing or below the supported floor. Run
  `hankpdf --doctor` for the full report. Exit code 17
  (`E-ENV-MISSING`).
- **Per-page worker memory caps with cooperative shutdown.** Linux
  uses `RLIMIT_AS`; Windows ≥ 8 uses Job Object self-assign via ctypes
  (no pywin32). macOS attempts `RLIMIT_AS` but the kernel rejects it
  — falls back to the watchdog. Default cap formula:
  `min(max(8 GB, 16 × input_size), 16 GB)`, further clamped by an
  aggregate-envelope check against
  `psutil.virtual_memory().available × 0.7 / n_workers`. A parent-side
  psutil RSS watchdog runs as a backstop; when it observes a worker
  exceeding the cap, it sets a shared `multiprocessing.Event` and ALL
  in-flight workers cooperatively drain at the next safe-write
  boundary (no SIGTERM mid-write — that path corrupts partial output
  streams). Override via `--max-worker-memory-mb` or
  `CompressOptions.max_worker_memory_mb`. Exit code 18 (`E-MEM-CAP`).
- **`HostResourceError` + exit code 19 (`E-HOST-RESOURCE`).** Raised at
  startup when the aggregate-envelope check determines that
  `cap × n_workers` would exceed 70% of available host RAM. Distinct
  from `MemoryCapExceededError` (worker died from cap) — the host has
  insufficient memory before any worker spawns. Reduce `--max-workers`
  or free memory; jobs that previously OOM-killed the host now refuse
  cleanly at startup.
- **`--max-worker-memory-mb INT` CLI flag.** Per-worker cap override in
  megabytes. Pass `0` to disable (test escape hatch).
- **`--correlation-id ID` CLI flag.** Format-validated
  (`[A-Za-z0-9._:-]{1,64}`). Threaded into
  `CompressReport.correlation_id` and stamped onto every stderr line
  via the existing `corr=` prefix mechanism.
- **`--preserve-signatures` flag.** Signed-PDF passthrough alternative
  to `--allow-signed-invalidation`. Mutually exclusive.
- **`correlation_id` kwarg on `compress_stream()`.** Validated against
  `[A-Za-z0-9._:-]{1,64}`. Argument validation runs BEFORE the env
  check, so bad formats produce `ValueError` not `EnvironmentError`.
- **Decompression-bomb regression corpus.** 3 deterministic fixtures
  (`huge_page_dimensions` → exit 16, `xref_loop` → exit 13,
  `objstm_explosion` → exit 12) plus an on-demand `length_mismatch`
  generator. Each refusal exits with a pinned structured code in
  under 30 s. Wires `MAX_PAGE_AXIS_PT = 14400` (200 inches) check in
  `_enforce_input_policy` for the huge-MediaBox fixture.
- **`signature_state` and `signature_invalidated` on `CompressReport`.**
  Schema v5 (additive — old consumers continue to work).
- **`worker_memory_cap_bytes` and `worker_peak_rss_max_bytes` on
  `CompressReport`.** Visibility into the cap that fired.
- **`PolicyDecision` enum** — public API (re-exported via
  `hankpdf/__init__.py:__all__`). Returned by `_enforce_input_policy`
  to flag passthrough requests instead of raising a sentinel exception.
- **`_environment.py`, `sandbox/platform_caps.py`,
  `engine/per_page_gate.py`** — new internal modules; not part of the
  public API.

### Changed

- **qpdf floor enforced at 11.6.3.** Was documented in
  `docs/ENVIRONMENT.md`; now hard-checked at startup. Lower versions
  abort with exit 17.
- **OpenJPEG floor enforced at 2.5.4** (CVE-2025-54874). Probed via
  Pillow encode test.
- **Internal refactor:** the per-page MRC gate moved out of
  `hankpdf/__init__.py` into the new `hankpdf.engine.per_page_gate`
  module. No behavior change.

### Migration

```python
# Was (0.2.x):
from pdf_smasher import compress

# Now (0.3.0):
from hankpdf import compress
```

If you may have lingering `pdf_smasher` imports, run your test suite
under `PYTHONWARNINGS=error::DeprecationWarning` against 0.2.x first
to flag every call site. The 0.3.0 wheel does not ship the shim.

```python
# 0.2.x: signed PDF compressed by default (silently invalid signature).
hankpdf.compress(signed_input)  # signature destroyed, no warning
# 0.3.0: signed PDF refused by default; pick one:
hankpdf.compress(signed_input, options=CompressOptions(preserve_signatures=True))
hankpdf.compress(signed_input, options=CompressOptions(allow_signed_invalidation=True))
```

### Dependencies

- New runtime dep: `psutil>=5.9,<8` (RSS watchdog).
- New test-only dep: `pyhanko>=0.27,<1` (signed-PDF fixture generation).

## [0.2.2] - 2026-04-29

**No code changes** — terminology refresh requested by the CTO. The PyPI project description, GHCR image label, `hankpdf --help` output, module docstring, and all docs now describe HankPDF as a **PDF compressor** rather than a "PDF shrinker." Reasoning: "shrinking" implies pixel resize / dimensional reduction, which is one operation in the pipeline (background-layer DPI downsampling) but not the product. The product is byte-level compression via codec changes (JBIG2 / JPEG / JPEG2000), MRC layering, and foreground/background segmentation.

### Changed

- README tagline, `pyproject.toml [project].description`, `docker/Dockerfile org.opencontainers.image.description`, `hankpdf/__init__.py` module docstring, `hankpdf/cli/main.py` argparse description, and the lead lines / section headers in `docs/ARCHITECTURE.md`, `docs/SPEC.md`, `docs/PERFORMANCE.md`, `docs/THREAT_MODEL.md` all use "compress" / "compressor" terminology consistently. `tests/integration/test_ratio_gate.py:311` switched "shrink" → "reduce" (that comment was about page count, not bytes).
- README status line + GHCR tag references bumped to `0.2.2`.

### Preserved (deliberate)

- CHANGELOG historical entries — frozen text describing what each version actually shipped with.
- `docs/superpowers/{plans,specs}/*` — write-time snapshots; rewriting history misrepresents what was reviewed.

After this release: `grep -rn "shrink\\|shrunk\\|shrinking\\|shrinker"` outside CHANGELOG / superpowers returns no hits.

## [0.2.1] - 2026-04-28

**No code changes** — doc-only patch release to refresh the PyPI project page with content that landed after `0.2.0` was published. PyPI distribution metadata (project description, README) is immutable per-version, so the install prompt and the corrected GHCR tag references on the PyPI page require a version bump.

### Changed

- **PyPI project README** now includes the **"Install via Claude Code / Codex / any coding agent (easiest, no clone needed)"** section — a paste-into-agent prompt that walks a fresh-machine bootstrap on Windows / macOS / Linux from zero (no Python, no native deps) to `hankpdf in.pdf -o out.pdf` working in a terminal. Detects OS, installs Python 3.14 + uv + Tesseract + qpdf + jbig2enc, creates an isolated venv at `~/.hankpdf-env`, `pip install hankpdf`, verifies via `hankpdf --doctor`. Stops to ask for sudo / GitHub auth / WSL activation. The clone-based dev-flow prompt is preserved below the new section.
- **GHCR tag references** in the README now use `:0.2.1` (no `v` prefix) instead of `:v0.2.1`. The metadata-action in `docker.yml` strips the `v` prefix from semver patterns; published image tags are `0.2.1`, `0.2`, `latest`, `sha-…`. The `:v…` references in 0.1.0/0.2.0 release notes were always inaccurate copy.

### Fixed (in v0.2.0 docker image, by retag)

The v0.2.0 GHCR image originally pushed missed the canonical `hankpdf/` package because `docker/Dockerfile:198` only copied the legacy `pdf_smasher/` shim into the build context. This was fixed in PR #21 and the v0.2.0 git tag was force-updated to point at the corrected source. The retagged v0.2.0 image (digest `sha256:2144c8c…`) is signed and SLSA-attested under the new commit. PyPI `hankpdf 0.2.0` was unaffected — the wheel is built directly from `pyproject.toml` with the full source tree.

## [0.2.0] - 2026-04-28

**Distribution rename: `pdf-smasher` → `hankpdf`.** The CLI command, GHCR image, product brand, and now the PyPI distribution + Python import package are all `hankpdf`. The previous split (`pip install pdf-smasher` / `hankpdf in.pdf`) was confusing for new users; consolidating to a single name end-to-end. `pdf-smasher 0.1.0` on PyPI is yanked (existing pins still install with a warning per PEP 592; bare `pip install pdf-smasher` no longer resolves).

### Migration

```bash
# was
pip install pdf-smasher
# now
pip install hankpdf
```

```python
# was
from pdf_smasher import compress, CompressOptions
from pdf_smasher.types import CompressReport

# now
from hankpdf import compress, CompressOptions
from hankpdf.types import CompressReport
```

The `pdf_smasher` import package is preserved as a **deprecation shim** for one cycle — every import re-exports the public API from `hankpdf` and emits a `DeprecationWarning` pointing at the new name. Scheduled for removal in **0.3.0**.

After pulling: `rm -rf .venv .mypy_cache .pytest_cache && uv sync` to clear cached PKG-INFO from the old dist name.

### Wheel filename change (BREAKING for CI scripts that pin filenames)

- Old: `pdf_smasher-0.1.0-py3-none-any.whl`
- New: `hankpdf-0.2.0-py3-none-any.whl`

Anyone pinning the wheel filename in CI or air-gapped install scripts must update.

### Cosign verify recipe

The `--certificate-identity-regexp` for verifying signed Docker images now must allow either repo path during the transition (the GHCR image name `ghcr.io/hank-ai/hankpdf` is unchanged and was always branded that way). See updated examples in `README.md` and `docker/README.md`.

### Repo also renamed

`hank-ai/pdf-smasher` → `hank-ai/hankpdf` (round-trip from a brief detour earlier in the day). GitHub redirects keep old URLs working; new clones / GitHub UI links use the canonical name.

### Added
- New canonical Python package `hankpdf/`. All API entries (`compress`, `compress_stream`, `triage`, `CompressOptions`, `CompressReport`, `BuildInfo`, `VerifierResult`, `TriageReport`, `__version__`, `__engine_version__`) re-exported from package root.
- Deprecation shim package `pdf_smasher/` — `from pdf_smasher import *` still works for one release cycle; emits `DeprecationWarning` with migration text.
- `tests/integration/test_install_smoke.py` — 4 tests guarding the rename: canonical import works without warning, legacy shim emits the right warning, `importlib.metadata.version("hankpdf")` matches `hankpdf.__version__`, and a clean-venv wheel install end-to-end smoke (`hankpdf --version` resolves correctly, both import paths work in a freshly-installed Python).

### Changed
- `pyproject.toml`: `name = "pdf-smasher"` → `name = "hankpdf"`. `[project.scripts] hankpdf = "pdf_smasher.cli.main:main"` → `"hankpdf.cli.main:main"`. `[tool.hatch.build.targets.wheel] packages` ships **both** `hankpdf` and `pdf_smasher` (the shim) for one cycle. Project URLs flipped back to `github.com/hank-ai/hankpdf` (post-repo-rename).
- `hankpdf/_version.py`: `_dist_version("pdf-smasher")` → `_dist_version("hankpdf")`. `_DEV_VERSION` `"0.1.0"` → `"0.2.0"`. The previous string would have caused `hankpdf --version` to silently report the dev fallback (`0.1.0`) on every installed wheel — a stealth failure caught by the new install-smoke test.
- `uv.lock` regenerated under the new dist name.
- `.github/workflows/docker.yml` path filter `pdf_smasher/**` → `hankpdf/**` so changes to the renamed package keep triggering image rebuilds.
- All ruff per-file overrides in `pyproject.toml` and all docs/tests/scripts paths updated from `pdf_smasher/...` to `hankpdf/...`.

## [0.1.0] - 2026-04-28

First public release. Pre-1.0 SemVer applies — anything may break between minor versions until 1.0. CLI + library APIs are documented in `docs/SPEC.md`; `CompressReport.schema_version` is the wire-contract version (currently `4`).

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
- Replaced placeholder `ourorg/pdf-smasher` URLs with the real `hank-ai/pdf-smasher` URLs across `pyproject.toml`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`.
- Removed placeholder `security@TBD.example` from `SECURITY.md`. GitHub Security Advisories is now the sole reporting channel.
- Storage-agnostic corpus mirror story (`s3_mirror` field renamed to `mirror_url`; docs no longer assume S3).
- README test count and Docker-image tag examples updated to reflect reality.
