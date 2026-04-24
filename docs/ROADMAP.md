# HankPDF — Implementation Roadmap

Phased build plan. Each phase has: **goal**, **deliverables**, **acceptance criteria**, and a **task list** with checkboxes. Work top-to-bottom unless dependencies allow parallelization (noted explicitly).

A task is done when every checkbox in it is ticked AND the phase's acceptance criteria are verified.

---

## Phase 0 — Decisions and scaffolding

**Goal**: resolve open questions, stand up the repo, establish project conventions. No engine code yet.

**Deliverables**: signed-off decisions, empty repo with build/lint/test scaffolding, CI green on a placeholder test, test corpus in place.

**Acceptance criteria**:
- Every open question in ARCHITECTURE.md §13 has a recorded answer.
- `pytest`, `ruff`, `mypy` all pass on a no-op placeholder module.
- GitHub Actions matrix (Linux, macOS, Windows × Python 3.14) runs on every PR.
- Test corpus directory contains at least 10 public-domain PDFs with documented provenance.

### Tasks

**T0.1 — Pre-implementation decisions** *(all decided; captured in ARCHITECTURE §13)*

- [x] Product brand = HankPDF; repo name `pdf-smasher`; CLI binary `hankpdf`.
- [x] Safe mode = explicit opt-in via `--mode safe` or API `mode="safe"`. Default = `standard`.
- [x] Test corpus strategy = URL-referenced (manifest JSON with filename + upstream URL + SHA-256; no Git LFS).
- [x] **HankPDF is a local tool**, not a service. No server-side pipeline, no tenant system, no BAA — users who embed it in their own pipeline own that pipeline's compliance.
- [x] **CPython 3.14 standard GIL for v1.** (Reversed from earlier free-threaded decision. Research found pypdfium2, pikepdf, opencv, and lxml have no `cp314t` wheels in April 2026; pypdfium2 maintainers explicitly say pdfium is not thread-safe. `python3.14t` would silently re-enable the GIL. Parallelism uses `multiprocessing`, not threads. Revisit v1.1.)
- [x] Corpus seed = loose set of oversized public PDFs from the open internet; grow as edge cases emerge.
- [x] Repo visibility = private for now; go public when ready.
- [x] License = Apache-2.0 (permissive, attribution required).
- [x] DCO/CLA — moot while private; revisit at open-sourcing.

**T0.2 — License and contributing files**
- [ ] `LICENSE` = Apache-2.0 at repo root.
- [ ] `NOTICE` listing copyright (our author line) + every third-party attribution required by the bundled stack.
- [x] `CONTRIBUTING.md` with dev setup, test, and PR conventions (internal-only while private; kept current so it's ready on open-sourcing). *(commit-type allowlist expanded in Wave-1 DCR)*
- [ ] `SECURITY.md` — how to report vulnerabilities, disclosure policy, signing key fingerprints once known.
- [ ] `CODE_OF_CONDUCT.md` — deferred until public (not needed while repo is private).

**T0.3 — Python project scaffolding**
- [ ] `pyproject.toml` — PEP 621, `requires-python = ">=3.14"`. Standard-GIL CPython 3.14. Pinned deps via lock file.
- [ ] Use `uv` as package manager (fast, lockfile, cross-platform).
- [ ] CI matrix: `python3.14` (GIL) across Linux/macOS/Windows.
- [ ] At v1.1: add a `python3.14t` canary job once pypdfium2, pikepdf, opencv, and lxml publish `cp314t` wheels (tracked in a separate follow-up issue, not blocking v1).
- [ ] Directory layout:
  ```
  pdf_smasher/
    __init__.py
    engine/         # core compression: rasterize, segment, encode, compose
    cli/            # argparse entrypoint, report formatters
    verifier/       # OCR diff, SSIM, structural audit
    sandbox/        # subprocess resource caps, timeout wrappers
    utils/          # redaction, filename normalization, hashing
  tests/
    unit/
    integration/
    corpus/         # manifest.json + fetch script; actual files gitignored
  scripts/          # benchmarks, corpus fetchers, ad-hoc tools
  docker/           # Dockerfile + entrypoint
  docs/             # already exists
  ```
- [ ] `ruff` config in `pyproject.toml` — line length 100, select = `["ALL"]` then ignore known noise.
- [ ] `mypy` strict config.
- [ ] `pytest` config — `testpaths`, markers for `slow`/`integration`/`corpus`.
- [ ] `pre-commit` hooks for ruff, mypy, pytest (unit only).

**T0.4 — CI setup**
- [ ] `.github/workflows/ci.yml` — matrix Linux/macOS/Windows × Python 3.14.
- [ ] Jobs: lint → unit tests → integration tests (corpus) → benchmark (advisory, not blocking).
- [ ] Cache: uv, apt, Homebrew, Chocolatey for native deps.
- [ ] Dependabot for Python + GitHub Actions.
- [ ] `CODEOWNERS` if private team repo.

**T0.5 — Native dep strategy**
- [ ] Document where each native binary comes from at build time:
  - Linux: apt packages (pre-existing) or compiled from source in a prep step.
  - macOS: Homebrew formulae + re-signed in the installer bundle.
  - Windows: pre-built binaries cached in the repo under `vendor/windows/`.
- [ ] Vendor `jbig2enc` source (Apache-2.0); build in CI for each platform.
- [ ] Test strategy: `hankpdf --doctor` subcommand reports which deps resolved to which version on the current system.

**T0.6 — Test corpus**
- [ ] Create `tests/corpus/manifest.json` — each entry has `filename`, `url`, `sha256`, `tags` (color/mono/mixed/linearized/tagged/large/small/weird), `license`, `notes`.
- [ ] Seed with ~10–20 oversized public PDFs from the open internet. Simple grab — Internet Archive book scans, govinfo.gov large reports, USPTO patents with diagrams. Cover a rough spread: color scans, monochrome text, mixed, tagged, linearized, ≥100 pages, ≤10 pages.
- [ ] Generate synthetic "weird" fixtures under `scripts/generate_corpus/` — corrupt xref, JBIG2-in-stream, signed, encrypted (known password), oversize-page-dimension. Reproducible.
- [ ] `scripts/fetch_corpus.py` — downloads manifest files to `tests/corpus/_cache/` with SHA verification; idempotent; used by CI and developers.
- [ ] Corpus S3 bucket provisioned under our account; original upstream URLs kept in the manifest as the canonical source but S3 mirror ensures availability.

**T0.7 — Test strategy document**
- [ ] Write `tests/STRATEGY.md` covering: unit vs integration vs corpus vs benchmark tiers, determinism requirements, how to add a new corpus fixture, how to interpret ratio-band tests.
- [ ] Decide SSIM/Levenshtein threshold calibration approach: per-corpus-class tuned or global.

**T0.8 — Security/privacy baselines**
- [ ] Commit a threat-model diagram (Mermaid) as `docs/THREAT_MODEL.md` summarizing attacker → asset → mitigation.
- [ ] Define log redaction policy (see SPEC §9.2); implement a `redact()` helper stub even before logging is used.

**T0.9 — Environment-floor assertions (`--doctor` + boot checks)**

Replaces the retired free-threaded smoke-test. Every invocation starts with a floor check; missing or out-of-date deps fail loud with exit 41 (EnvironmentError) and a concrete fix message.

- [ ] **qpdf version floor ≥ 11.6.3** — 11.0.0–11.6.2 had a silent character-drop bug in `\d` octal escapes inside binary strings (qpdf #1050 / Launchpad #2039804). Corrupts `/ID`, XMP metadata in encrypted files, bookmark names, form-field values, even encryption keys. Fail to start if violated.
- [ ] **pdfium binary revision recorded at startup** — pypdfium2 bundles a pinned chromium revision; log it + assert it matches the lockfile value (prevents silent drift between `pip install`s).
- [ ] **OpenJPEG ≥ 2.5.4** — CVE-2025-54874 OOB heap write in ≤2.5.3.
- [x] **Pillow `MAX_IMAGE_PIXELS` explicitly set** — not left at library default; size chosen relative to our `max_input_mb` budget. Assert at module import. *(set at import in Wave-1 DCR; was documented but not actually applied)*
- [ ] **`sys._is_gil_enabled()` == True** at process start — if we claim standard GIL build and it reports disabled, fail loud; if it silently flipped due to a dep re-enable, we want to know.
- [ ] **Tesseract language pack present and pinned by SHA-256** — distro defaults silently swap `tessdata_fast` vs `tessdata` vs `tessdata_best` between versions.
- [ ] **jbig2enc vendored + build-sha recorded** — agl/jbig2enc is unmaintained upstream; we vendor a specific commit, build in CI, and record the commit hash in `--doctor` output.
- [ ] **qpdf + pdfium + Tesseract versions printed** by `--doctor` along with Python version, platform, CPU count, memory, and free-thread-build flag (should be "not built").
- [ ] Document assertions and fix-hints in `docs/ENVIRONMENT.md`.

---

## Phase 1 — Engine spike (de-risk the whole bet)

**Goal**: prove the MRC algorithm hits target compression on a real scan, end-to-end, before any architectural investment.

**Deliverables**: a `scripts/spike_mrc.py` script that processes one real PDF through the full MRC pipeline and emits a valid output PDF + a ratio number.

**Acceptance criteria**:
- Script runs end-to-end on at least 3 corpus inputs representing color scans, monochrome text, and mixed content.
- Output PDFs open in Adobe Acrobat, macOS Preview, and Chrome PDF viewer with no visual regression obvious to a human reviewer.
- Ratios measured: ≥5× on color scans, ≥20× on monochrome text, ≥3× on mixed. Below these, **stop Phase 2** and diagnose.
- OCR text layer is searchable in Acrobat.
- Output passes veraPDF PDF/A-2u validation on at least one input.

### Tasks

**T1.1 — Rasterize via pdfium**
- [ ] Wire up `pypdfium2`. Open PDF, iterate pages, get PIL images at configurable DPI.
- [ ] Verify rotation metadata preserved.
- [ ] Verify CMYK inputs produce sensible raster (may need conversion heuristic for pdfium).

**T1.2 — Segmentation: mask generation**
- [ ] Run Tesseract via `pytesseract`, get hOCR. Extract word bounding boxes.
- [ ] OpenCV adaptive threshold (Sauvola or Niblack) on grayscale raster.
- [ ] Union: word-box union + adaptive-threshold pixels outside word boxes.
- [ ] Morphological close. Output 1-bit PIL mask.
- [ ] Sanity-check: visualize mask overlay on page raster; looks reasonable.

**T1.3 — Foreground layer**
- [ ] Extract foreground pixel values (text color) at mask locations.
- [ ] For v1 spike: use global median ink color — single color for all text on the page.
- [ ] Encode mask and (global-color) foreground as JBIG2 generic region via `jbig2enc` (subprocess call).
- [ ] Sanity-check: foreground JBIG2 bytes << raw mask bits.

**T1.4 — Background layer**
- [ ] Inpaint mask-1 pixels: simple nearest-neighbor or Gaussian-hole-fill.
- [ ] Downsample to 150 DPI.
- [ ] Encode as JPEG2000 via OpenJPEG (`glymur` wrapper or subprocess) OR JPEG via Pillow for v1 spike.

**T1.5 — Compose PDF**
- [ ] Use pikepdf to build output PDF:
  - Create image XObjects for mask, foreground, background.
  - Construct page content stream using `/SMask` to apply mask to foreground.
  - Background drawn first (full-page), foreground over mask drawn on top.
- [ ] Embed OCR text in invisible rendering mode over the page using hOCR positions.
- [ ] Write output.
- [ ] Open output in 3 readers; verify visually.

**T1.6 — Measure**
- [ ] Script prints: input bytes, output bytes, ratio, pages, wall time, OCR text layer present y/n.
- [ ] Run on 3+ corpus inputs; log results.
- [ ] Decision gate: ratios meet acceptance criteria?

**T1.7 — Document the spike**
- [ ] `docs/SPIKE_REPORT.md` — what worked, what didn't, what lessons to carry into Phase 2.
- [ ] List any algorithmic tweaks needed before Phase 2 (e.g., per-region foreground color instead of global median, different mask kernel size, different background DPI).

---

## Phase 2 — Engine core

**Goal**: turn the spike into a production-quality engine module with the full pipeline (Triage → Sanitize → Recompress → Verify) and the weird-PDF handling from SPEC §4.

**Deliverables**: `pdf_smasher.engine` package exposing `compress()` and `triage()` per SPEC §1.

**Acceptance criteria**:
- Every weird-PDF taxonomy row in SPEC §4 has a unit test using a dedicated corpus fixture.
- Full corpus run: ≥90% of inputs hit their expected ratio band; 100% either pass or return a structured refusal (no crashes).
- Verifier rejects at least 3 deliberately-corrupted outputs (mutated bytes in the middle of the image stream) and accepts legitimate outputs.
- Performance within 3× of the spike's single-page timing (overhead for verifier + triage + sanitize).
- 100% type-checked (`mypy --strict`).

### Tasks

**T2.1 — Triage module**
- [ ] Implement all detections in SPEC §4 (encryption, signatures, filter chain, OCGs, tagging, PDF/A, etc.).
- [ ] Emit `TriageReport` dataclass.
- [ ] Unit test: each detection against a targeted fixture.
- [ ] Integration test: full corpus run, assert expected classification per known input.

**T2.2 — Sanitize module**
- [ ] Implement strip operations (`/JavaScript`, `/OpenAction`, `/AA`, `/Launch`, `/EmbeddedFiles`, `/RichMedia`, `/GoToR-External`).
- [ ] Implement password decrypt path (in-process, no argv).
- [ ] Implement linearization removal.
- [ ] Implement corrupt-xref repair path.
- [ ] Unit tests per operation.

**T2.3 — Recompress module — segmentation refinement**
- [ ] Per-region foreground color (not global median). Color averaged per connected component.
- [ ] Color-page detection heuristic — if a page has significant non-grayscale content, use color foreground; else monochrome.
- [ ] Handwriting handling — extend word boxes with looser grow kernel; add configurable `mode=medical` parameter for stricter mask preservation.
- [ ] Small-print detection — flag pages where Tesseract confidence is below threshold; skip MRC and use full-page JPEG at higher quality (recorded as warning).
- [ ] Unit tests on synthetic mask scenarios.

**T2.4 — Recompress module — codec wiring**
- [ ] JBIG2 generic region coding (no `-r`, no symbol mode) — wrap `jbig2enc` as a subprocess call with strict argument allowlist.
- [ ] OpenJPEG wrapper — via `glymur` or subprocess. Configurable quality.
- [ ] JPEG fallback via Pillow.
- [ ] Codec selection policy per page class.

**T2.5 — Recompress module — PDF assembly**
- [ ] Build per-page `/SMask` image XObject construct.
- [ ] Invisible OCR text layer embedding.
- [ ] Preserve CMYK, rotation, OCGs, Form XObjects, tag tree.
- [ ] Re-linearize if input was linearized.
- [ ] Stitch pages with qpdf `--empty --pages`.

**T2.6 — Verifier module** *(core shipped in Phase-2b / Wave-1 DCR)*
- [x] OCR Levenshtein per page. *(reading-order-insensitive bag-of-lines variant shipped)*
- [x] Global SSIM + tile-level SSIM (50×50).
- [x] Numeric-token confidence delta → replaced with digit-multiset exact-match (see T2.15).
- [x] Structural audit (page count, annots, forms, sigs, attachments).
- [x] `VerifierResult.status` widened to include `"skipped"` with fail-closed sentinel metrics.
- [ ] Threshold config per `mode` (fast / standard / medical). *(thresholds exist; per-mode tuning not yet complete)*
- [ ] Unit tests: assert pass on legitimate output, fail on tampered output.

**T2.7 — Provenance / sidecar manifest**
- [ ] Emit `.hankpdf.json` sidecar per SPEC §6.
- [ ] Unit tests.

**T2.8 — Idempotency canonicalization**
- [ ] Implement canonicalization per SPEC §5.1.
- [ ] Unit test: two byte-different, content-identical PDFs canonicalize to the same hash.
- [ ] Unit test: two content-different PDFs canonicalize to different hashes.

**T2.9 — Sandboxing wrappers** *(timeout model shipped in Wave-1 DCR)*
- [x] Three-layer timeout model implemented: `per_page_timeout_seconds`, `total_timeout_seconds`, and OCR timeout. *(Wave-1 DCR)*
- [ ] Process-level resource caps: `RLIMIT_AS`, `RLIMIT_CPU` in the engine subprocess. *(timeouts are in place; hard RLIMIT caps remain for T4.1)*
- [ ] Graceful SIGKILL on resource-cap exceed → `MaliciousPDFError`.
- [ ] Unit tests with synthetic "bomb" fixtures.
- [ ] For Docker image: seccomp profile + non-root user + read-only rootfs (T4.7). Host-level sandbox is the user's responsibility.

**T2.10 — Performance pass** *(parallelism shipped in Phase-2b)*
- [ ] Profile with `py-spy` or `scalene`; identify top hotspots.
- [x] Parallel page processing via `ProcessPoolExecutor` with `forkserver` start method. DoS cap on `--max-workers` enforced. *(Phase-2b)*
- [ ] Document expected per-page wall time at default settings.

**T2.11 — Per-page strategy selector**
- [ ] Implement classifier (ARCHITECTURE §4.3.1): already-optimized, text-only, photo-only, mixed.
- [ ] Already-optimized detection: image XObjects contain existing `/JBIG2Decode` + `/SMask` pattern OR DCT quality heuristic <40 → pass-through.
- [ ] Text-only detection: mask coverage >95% + grayscale bg variance <5% → single JBIG2 page image (no SMask, no bg layer).
- [ ] Photo-only detection: mask coverage <5% + uniform high-frequency bg → single JPEG2000 page image.
- [ ] Emits `strategy_distribution` metric dimension.
- [ ] Unit tests with synthetic fixtures per class.

**T2.12 — CMYK pre-pass**
- [ ] Detect CMYK pages via pikepdf ColorSpace inspection before rasterization.
- [ ] Managed CMYK→sRGB conversion via `littlecms` (lcms2, MIT-style license) with bundled sRGB profile.
- [ ] Regression: compare rendered output of CMYK input with vs. without pre-pass — pre-pass must produce visibly-closer-to-Acrobat colors.
- [ ] Skip pre-pass on pages whose output target is CMYK-preserving (rare; config flag).

**T2.13 — pdfium thread/memory hygiene**
- [ ] Process-global `threading.Lock` wrapping every pypdfium2 call site (pdfium not thread-safe).
- [ ] Chunk-and-reopen `PdfDocument` every 20–50 pages (pdfium #669 monotonic leak).
- [ ] Explicit `pdf.close()` in try/finally everywhere; no reliance on weakref finalizers.
- [ ] Enforce max-page-dimensions cap (14000 px on any axis); downsample or refuse above.
- [ ] Transparency-heavy page detector via pikepdf object walk; adaptive DPI or pass-through for pages with >N transparency groups.

**T2.14 — Signed-PDF preservation path**
- [ ] Detect signed PDFs (`/AcroForm /SigFlags` bit 1 OR `/Sig` fields).
- [ ] For signed inputs without `--allow-signed-invalidation`: save via pikepdf incremental-update mode (not full rewrite) to preserve `/ByteRange` integrity, OR pass-through entirely.
- [ ] For certifying signatures (`/Perms /DocMDP`): require the separate `--allow-certified-invalidation` flag; audit-log every invalidation.
- [ ] Strip `/SigFlags` bit 1 only when signature is intentionally invalidated (avoid "broken signature!" viewer warnings on PDFs where we opted in).
- [ ] Tests against a corpus of signed fixtures.

**T2.15 — Verifier hardening per latest research** *(core items shipped in Wave-1 DCR)*
- [x] Replace numeric-confidence gate with digit-multiset exact-match. *(Wave-1 DCR)*
- [x] Implement reading-order-insensitive Levenshtein (bag-of-lines best-match). *(Wave-1 DCR)*
- [ ] Pre-OCR small-print detector: histogram connected-component x-height; if p50 <12 px → upscale 2× (OpenCV INTER_CUBIC) OR route to full-JPEG safe mode.
- [ ] Handwriting-region detector independent of Tesseract (OpenCV CC + stroke-width variance) — seed mask regions Tesseract won't return.
- [x] Invalid-UTF-8 resilience: decode with `errors='replace'` both sides. *(Wave-1 DCR)*
- [x] Decompression-bomb guard: `PIL.Image.MAX_IMAGE_PIXELS` set at import + pre-allocation guard before decompression. *(Wave-1 DCR; also satisfies T0.9 Pillow check)*
- [ ] Stamp/watermark symmetry: if we detect low-saturation overlays, apply same despeckle to both input and output OCR passes (symmetric noise).
- [ ] Cross-host drift measurement: run verifier on macOS + Linux CI; record tolerance budget.

**T2.16 — Phase-2b features + Wave-1–4 DCR remediation** *(all shipped; PR #3 / branch feat/dcr-wave-1-remediation)*

These items were implemented as part of the Phase-2b build-out and the four-wave DCR remediation pass.

- [x] Image-export mode (`--output-format jpeg|png|webp`) via `iter_pages_as_images` streaming generator.
- [x] Auto-chunked output (`--max-output-mb`); zero-padded 1-indexed chunk/image filenames with pad width scaled past 999.
- [x] Input-policy gate (`_enforce_input_policy`) applied uniformly to MRC and image-export paths.
- [x] DoS caps enforced on `--image-dpi`, `--pages` range + total cardinality, `--max-output-mb`, `--max-workers`.
- [x] Atomic `.partial` + rename writes (no partial output visible to callers on failure or interrupt).
- [x] Passthrough floors (`min_input_mb`, `min_ratio` default 1.5) to avoid expanding already-small or already-compressed files.
- [x] Stable `[W-*]` warning and `[E-*]` error codes on every stderr line.
- [x] Sidecar manifest schema bumped to v2; migration note added to SPEC §11.1.

---

## Phase 3 — Python API and CLI

**Starting condition**: Phase-2b engine core (parallel processing, verifier, image-export, chunked output, DCR Wave-1–4 remediation) is complete as of PR #3.

**Goal**: ship the engine behind a stable CLI and importable Python package. This is the first "something real to ship" phase.

**Deliverables**: `pip install pdf-smasher` works (package name stays `pdf-smasher`). `hankpdf --help` prints a proper help screen. Exit codes and JSON report schema match SPEC §2.

**Acceptance criteria**:
- Full SPEC §2 CLI contract implemented.
- Every exit code is triggerable from a corpus-backed test.
- `--doctor` subcommand correctly reports missing/misconfigured deps.
- Stdin/stdout streaming works (tested with `cat foo.pdf | hankpdf -`).
- Batch mode (`--in-dir` / `--out-dir`) with `--jobs` works, emits valid JSONL.

### Tasks

**T3.1 — Public Python API (`pdf_smasher/__init__.py`)**
- [ ] Export `compress`, `compress_stream`, `triage`, `CompressOptions`, `CompressReport`, all exceptions.
- [ ] API smoke test: `from pdf_smasher import compress; compress(bytes_read)` works.

**T3.2 — CLI entry point**
- [ ] `hankpdf` console script via entry_points in pyproject.toml.
- [ ] Argparse-based CLI with every SPEC §2.1 flag.
- [ ] Exit-code mapping per SPEC §2.2.

**T3.3 — Report formats**
- [ ] `--report text` (default, human-readable).
- [ ] `--report json` (single object).
- [ ] `--report jsonl` (one per file in batch).
- [ ] `--report none`.

**T3.4 — Streaming**
- [ ] `-` as stdin / stdout sentinel.
- [ ] Buffering policy per SPEC §2.4.

**T3.5 — Batch mode**
- [ ] `--in-dir` recursive scan.
- [ ] `--out-dir` mirrors structure.
- [ ] `--jobs` parallelism via `concurrent.futures.ProcessPoolExecutor`.
- [ ] JSONL output streams as jobs complete.

**T3.6 — `--doctor`**
- [ ] Print: Python version, HankPDF version, engine version, each native dep + version, platform info.
- [ ] Detect common misconfigs (missing Tesseract, missing jbig2enc, wrong version) and emit fix hints.

**T3.7 — Password handling**
- [ ] `--password-file` flag.
- [ ] `PDFSMASHER_PASSWORD` environment variable.
- [ ] Reject `--password` CLI flag entirely (emit error pointing to `--password-file`).
- [ ] Confirm password never appears in process title / ps output.

**T3.8 — Documentation**
- [ ] `docs/CLI.md` — the full CLI reference as a standalone doc (generated from argparse if possible).
- [ ] `docs/PYTHON_API.md` — API examples (encrypted input, batch, stdin/stdout, error handling).
- [ ] README quickstart section.

**T3.9 — Packaging**
- [ ] Publish to internal PyPI mirror (if applicable) — real PyPI at Phase 8.
- [ ] Reproducible wheel build via `python -m build`.

---

## Phase 4 — Hardening and hygiene

**Goal**: ensure the engine is safe to run on end-user machines — OOM-bounded, clean tempdir behavior, stable filename handling, correct signature preservation, weekly dependency refresh. No server infrastructure — HankPDF is a local tool.

**Deliverables**: robustness additions on top of the Phase 2–3 engine and CLI.

**Acceptance criteria**:
- Engine handles a corpus of pathological PDFs (decompression bombs, infinite-loop malforms, xref corruption, known-bad-producer output) without crashing the process; all failures return structured error codes.
- Signed PDFs round-trip without signature invalidation when `--allow-signed-invalidation` is not passed.
- No temp files remain after normal exit or SIGKILL drill.
- Weekly dep-refresh CI job open-able with a trivial diff.

### Tasks

**T4.1 — Resource caps per invocation**
- [ ] Linux CLI: `resource.setrlimit(RLIMIT_AS, ...)` on the engine subprocess to bound memory.
- [ ] Windows: Job Objects with break-away disabled for the engine subprocess.
- [ ] macOS: `RLIMIT_AS` via `resource` module.
- [ ] Per-page CPU timeout via `resource.RLIMIT_CPU` in child.
- [ ] Wall-clock watchdog (parent monitors child, SIGKILL on deadline).
- [ ] Verify OOM/timeout is bounded to one invocation, never takes down the user's shell or parent process.

**T4.2 — Tempdir hygiene**
- [ ] Every job runs in a `tempfile.TemporaryDirectory()` context manager.
- [ ] atexit handler removes dir even on SIGINT/SIGKILL (where reachable).
- [ ] CLI flag `--temp-dir PATH` for users with small `/tmp`.
- [ ] Test: kill the process mid-run; confirm no residual files remain (best effort — SIGKILL can't be intercepted, but TemporaryDirectory on the parent cleans up on normal exit).

**T4.3 — NFC filename normalization**
- [ ] Normalize all incoming/outgoing path strings to NFC (macOS filesystem stores NFD, Linux expects NFC, Windows NTFS varies).
- [ ] `filename_renormalized_total` counter in the CompressReport.
- [ ] Test: cross-platform roundtrip with non-ASCII filenames (`café.pdf`) — output filename matches expectation on each platform.

**T4.4 — Signature-preservation correctness**
- [ ] Corpus: at least 3 signed PDFs (simple, certifying-DocMDP, multi-signature).
- [ ] Round-trip through `compress()`: signature still validates with `pyHanko` / Acrobat when we pass-through.
- [ ] Test explicit opt-in invalidation paths (`--allow-signed-invalidation`, `--allow-certified-invalidation`): signature correctly marked invalid, `/SigFlags` stripped, sidecar manifest records invalidation.

**T4.5 — Weekly dep-refresh canary**
- [ ] CI job that detects new pypdfium2 release weekly, runs the golden corpus, auto-opens a PR with ratio-drift measurement.
- [ ] Same job checks Tesseract, qpdf, OpenJPEG, jbig2enc for new releases; raises an issue if versions drift from lockfile.
- [ ] Goal: no more than 1 week behind on CVEs.

**T4.6 — Decompression-bomb + resource-cap regression corpus**
- [ ] Synthetic fixtures covering: 1.2 billion-pixel page, xref infinite loop, 10k-object ObjStm, 100 MB stream with 2 GB declared `/Length`.
- [ ] Each fixture verifies the expected refusal with the correct exit code.

**T4.7 — Docker image polish**
- [ ] Slim base (distroless or wolfi).
- [ ] Non-root user (`hankpdf` UID 1000).
- [ ] Read-only rootfs with writable `/tmp`.
- [ ] `HEALTHCHECK` runs `hankpdf --doctor`.
- [ ] Tag image with engine version, Python version, pdfium revision; push to GHCR.

**T4.8 — Firefox-preview footgun documentation**
- [ ] Add a "Known reader quirks" note to the user docs: JBIG2 and some JPEG2000 streams render blank in Firefox's pdf.js. For workflows where the output will be previewed in Firefox, suggest `--legal-mode` (CCITT G4 instead of JBIG2) or `--bg-codec jpeg`.
- [ ] No webapp integration — just user-facing guidance.

---

## Phase 5 — (Removed — no GUI)

HankPDF is CLI-only. There is no desktop GUI phase. Users who want drag-drop behavior wire `hankpdf` into their OS shell themselves.

---

## Phase 6 — Packaging and publishing

**Goal**: a clean Python wheel on PyPI and a clean multi-arch Docker image on GHCR. No platform binaries, no code-signing.

**Deliverables**: CI produces a wheel + sdist + Docker image on every tag.

**Acceptance criteria**:
- `pip install pdf-smasher` on a clean Python 3.14 env works; `hankpdf --doctor` passes after the user has installed Tesseract + jbig2enc via their package manager.
- `docker run ghcr.io/ourorg/pdf-smasher:X.Y --doctor` works on a clean host with no extra setup.
- Release pipeline runs to green in under 15 minutes.

### Tasks

**T6.1 — Python wheel + sdist**
- [ ] Build wheel and sdist via `python -m build`.
- [ ] Declare runtime deps in `pyproject.toml`; native deps (Tesseract, jbig2enc) documented as system prerequisites in `docs/INSTALL.md`.
- [ ] Smoke test: `pip install dist/*.whl` on clean venvs across Linux / macOS / Windows, followed by `hankpdf --doctor`.
- [ ] Wheel type: pure-Python `py3-none-any` if it expresses native-dep declarations cleanly; per-platform wheels only if not.

**T6.2 — Docker image**
- [ ] Multi-arch build (amd64 + arm64) via `docker buildx`.
- [ ] Base: Debian slim or Chainguard wolfi.
- [ ] All native deps baked in: Tesseract + English tessdata, jbig2enc (vendored build), pdfium (via pypdfium2 wheel), qpdf, OpenJPEG, Pillow, opencv-python-headless.
- [ ] Non-root user (`hankpdf`, UID 1000).
- [ ] Writable `/tmp` only; rest of rootfs read-only friendly.
- [ ] Baked seccomp profile allowlisting only what our deps use.
- [ ] `HEALTHCHECK` runs `hankpdf --doctor`.
- [ ] Image size target ≤ 300 MB.
- [ ] Tag with engine version, Python version, pdfium revision; push to GHCR.

**T6.3 — `docs/INSTALL.md`**
- [ ] Document one-line system-dep install for each supported OS:
  - macOS: `brew install tesseract` (plus our vendored jbig2enc build instructions)
  - Debian/Ubuntu: `sudo apt install tesseract-ocr libtesseract-dev` plus vendored jbig2enc
  - Fedora/RHEL: `sudo dnf install tesseract` plus vendored jbig2enc
  - Windows: Chocolatey / winget / Scoop instructions, or use the Docker image
- [ ] Include `hankpdf --doctor` as the verification step.

**T6.4 — Release pipeline (`.github/workflows/release.yml`)**
- [ ] Tag-triggered: `vX.Y.Z` on main.
- [ ] CI builds wheel + sdist + Docker image.
- [ ] PyPI upload via GitHub OIDC trusted publishing (no long-lived API tokens).
- [ ] GHCR image push via GitHub OIDC.
- [ ] GitHub Release created with notes, SHA-256 checksums for wheel + sdist, and Docker image digest.
- [ ] Rollback plan: previous versions remain available at stable URLs (immutable PyPI versions + immutable GHCR digests + GitHub release retention).

---
## Phase 7 — User-facing docs and diagnostics

**Goal**: users can install, run, diagnose, and troubleshoot HankPDF on their own. No on-call rota, no hosted dashboards — HankPDF doesn't run anywhere we control.

**Deliverables**: user docs, CLI examples, diagnostics tooling, FAQ.

**Acceptance criteria**:
- Public API docs auto-generated from docstrings.
- CLI integration guide with bash + PowerShell wrapper examples for SFTP-upload workflows.
- Docker and `pip install` quickstart work end-to-end on clean macOS, Windows, and Linux hosts.
- `--doctor` produces a report support can actually triage from without remote access.

### Tasks

**T7.1 — User guides**
- [ ] `docs/CLI.md` — the full CLI reference (auto-generated where possible).
- [ ] `docs/PYTHON_API.md` — embedding examples (encrypted input, batch, stdin/stdout, error handling).
- [ ] `docs/SFTP_INTEGRATION.md` — bash + PowerShell sample wrappers that gate uploads on exit codes.
- [ ] `docs/DOCKER.md` — image usage, volume mounts, read-only rootfs example.
- [ ] `docs/SHELL_INTEGRATION.md` — how to wire `hankpdf` into Windows Explorer "Send To", macOS Shortcuts / Automator, or a Linux `.desktop` file for a user-built drag-drop workflow.
- [ ] `docs/FAQ.md` — top questions including "why did it refuse my signed PDF?", "why did size increase?", "how do I handle encrypted inputs?".
- [ ] `docs/TROUBLESHOOTING.md` — known reader quirks (Firefox/pdf.js blanks, macOS Preview SMask issue), what `--doctor` outputs mean.

**T7.2 — Diagnostics tooling**
- [ ] `hankpdf --doctor` prints platform/dep/version report.
- [ ] `hankpdf --doctor --report json` emits a machine-readable version for sharing with support.
- [ ] `hankpdf --report json` on a failing file produces enough context for remote triage without sending the PDF itself.

**T7.3 — Security disclosures**
- [ ] `SECURITY.md` with disclosure policy, signing-key fingerprints, release-signing workflow.
- [ ] GitHub Security Advisories enabled on the repo (when public).

---

## Phase 8 — Dogfood and calibration

**Goal**: run HankPDF against a real, diverse corpus to calibrate verifier thresholds and catch edge cases we didn't anticipate in synthetic tests.

**Deliverables**: empirical calibration data for SSIM per content class, digit-multiset hit rate, refusal-class distribution on real-world input.

**Acceptance criteria**:
- Runs cleanly against a dogfood corpus of 500+ PDFs sampled from the wild (see T0.6 seed corpus, plus any additional samples we legally have access to under our existing data agreements).
- Every refusal class observed in dogfood has a documented rationale ("expected / by-design" or "bug to fix before release").
- Verifier thresholds tuned to <2% false-positive rate on known-good-compressed outputs and >99% true-positive rate on deliberately-corrupted outputs.

### Tasks

**T8.1 — Corpus expansion**
- [ ] Grow the test corpus from the Phase 0 seed to at least 500 PDFs covering the content-class distribution we expect (heavy color scans, mono text, tagged, linearized, signed, encrypted, pathological producers).
- [ ] Document provenance for every file (URL + license).

**T8.2 — Verifier threshold calibration**
- [ ] Run the engine against the corpus; collect ratio, SSIM (global + tile-min), Levenshtein (raw + reading-order-insensitive), numeric-token match, refusal class.
- [ ] Plot distributions; adjust the standard/safe mode thresholds empirically.
- [ ] Document tuned values in ARCHITECTURE.md §5.

**T8.3 — Refusal-class triage**
- [ ] Every refusal-class bucket reviewed: "is this the right call, can we do better without sacrificing safety?"
- [ ] Algorithm tweaks land with regression runs; no unacknowledged behavior changes.

**T8.4 — Stress tests**
- [ ] Run the engine on the decompression-bomb corpus (T4.6); confirm bounded OOM.
- [ ] Run the engine on known-bad-producer outputs (Canon iR, Ricoh, Brother ADS); confirm no crashes, only structured refusals or successes.
- [ ] Run 200-page 800 MB corpus with `--jobs 4` on a 16 GB laptop without OOM.

---

## Phase 9 — Public release

**Goal**: cut v1.0, publish.

**Deliverables**: PyPI package, GHCR Docker image, signed GitHub releases with desktop installers, published user docs.

**Acceptance criteria**:
- Phase 8 dogfood run clean (≥95% successful, rest return structured refusals, zero crashes).
- All signing pipelines green.
- No open P0/P1 issues.
- `pip install pdf-smasher` → `hankpdf --doctor` works on clean macOS / Windows / Linux / Docker.

### Tasks

**T9.1 — Cut release**
- [ ] Tag v1.0.0 on main.
- [ ] CI produces: PyPI wheel + sdist, multi-arch Docker image.
- [ ] Artifact provenance attestations generated via GitHub OIDC.
- [ ] GitHub Release created with notes, SHA-256 checksums for wheel + sdist, and Docker image digest.

**T9.2 — Publish**
- [ ] PyPI upload.
- [ ] GHCR image push.
- [ ] GitHub release marked published (downloads served from GitHub CDN).
- [ ] Repo flipped to public (if that's the plan at that time).

**T9.3 — Announcement**
- [ ] Release notes.
- [ ] README and docs updated with the v1.0 link.
- [ ] Optional: blog post / Show HN thread.

**T9.4 — Post-release review**
- [ ] Monitor GitHub issues and PyPI download counts for the first two weeks.
- [ ] Plan v1.1 scope (free-threaded Python retry once the ecosystem catches up; additional OCR language packs; any user-requested features).

---

## Cross-cutting items

### Version control and release cadence

- Feature branches off `main`. PRs require green CI.
- Semantic versioning. Engine version bumps when compression algorithm changes produce byte-different outputs.
- Release notes generated from merged PRs.

### Dependency policy

- Pin all Python deps via lockfile. Review bumps weekly.
- Pin native deps by checksum where possible. Rebuild the Docker base monthly for security patches.
- Dependabot + GitHub Advisory Database for Python; manual tracking for native deps.

### Performance budgets (v1 targets)

- Single-page compression end-to-end: ≤ 2s at 300 DPI on modern x86.
- 200-page PDF: ≤ 5 min (single worker) server-side.
- Client UX: first-page progress update within 10 seconds of drop.

### Open questions logged later

Any ambiguity discovered during implementation that wasn't caught in Phase 0 goes here, to be resolved before the relevant phase starts.

| Question | Raised in | Status |
|---|---|---|
| _(none yet)_ | | |
