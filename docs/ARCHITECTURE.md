# HankPDF — Architecture

This document captures the design decisions and their rationale. For precise behavior specifications see [SPEC.md](SPEC.md). For algorithm and codec background see [KNOWLEDGE.md](KNOWLEDGE.md). For phased build plan see [ROADMAP.md](ROADMAP.md).

## 1. Problem statement

Scanned PDFs are massively oversized. Scanners default to 300 DPI color and embed JPEG pages at quality 90+, producing 200-page, 500 MB – 2 GB image-only PDFs where a 10 MB output would have been fine. These inputs inflate every downstream cost — upload bandwidth, storage, OCR, extraction — and often contain content (small print, handwriting, photos) where aggressive recompression risks silent content loss.

HankPDF ships as a **local command-line tool**: PDF in, compressed searchable PDF out. Runs on the user's machine. No network calls, no telemetry, no persistent storage beyond the output and an optional sidecar report. Distributed as:

1. **Python package** — `pip install hankpdf`. Brings `from hankpdf import compress` and the `hankpdf` CLI. User installs Tesseract + jbig2enc via their OS package manager (documented).
2. **Docker image** — sealed runtime with all native deps baked in. For CI/CD, SFTP wrappers, batch jobs, and any host where you'd rather not install Tesseract.

No GUI. No standalone binary. No platform installers. No code-signing. Users who want a drag-drop experience can wire `hankpdf` into their shell's "Send To" / Shortcuts / `.desktop` flow themselves.

## 2. Engine foundation — permissive stack only

Every component shipped to customers or called from our server must be under a license compatible with closed-source redistribution. AGPL and GPL dependencies are banned from the shipping path.

| Component | Role | License |
|---|---|---|
| **pdfium** (Google/Chromium) | PDF parsing + rasterization | Apache-2.0 + BSD-3 |
| **pypdfium2** | Python bindings for pdfium | Apache-2.0 |
| **qpdf** | PDF structure manipulation, split/merge, repair | Apache-2.0 |
| **pikepdf** | Python wrapper around qpdf | MPL-2.0 |
| **Tesseract 5** | OCR (text layer + word geometry) | Apache-2.0 |
| **jbig2enc** | 1-bit text/mask compression (generic region only) | Apache-2.0 |
| **libjpeg-turbo** | JPEG background encoding | BSD / IJG |
| **OpenJPEG** | JPEG2000 background encoding | BSD-2-Clause |
| **OpenCV (headless)** | Segmentation, thresholding, connected components | Apache-2.0 |
| **scikit-image** | SSIM verifier, image utilities | BSD-3 |
| **NumPy** | Array math | BSD-3 |
| **Pillow** | Python image I/O | HPND (MIT-like) |
| **Leptonica** (transitively via Tesseract) | Document image primitives | BSD-2-Clause |

**Explicitly excluded:**
- Ghostscript (Artifex AGPL)
- MuPDF (Artifex AGPL)
- PyMuPDF (Artifex AGPL)
- archive-pdf-tools (AGPL)
- Foxit / Apryse / ABBYY / Adobe SDKs (commercial, expensive, opaque pricing)

The exclusion of Ghostscript is the central licensing decision. It was the default "how you rasterize a PDF" for two decades in open source. Swapping to pdfium (a production-grade, permissive-licensed alternative that powers Chrome's PDF viewer) eliminates the entire AGPL problem without sacrificing capability.

## 3. Language and runtime

**CPython 3.14 (standard GIL)** for v1. Parallelism via **multiprocessing**, not threads.

**Why not free-threaded Python 3.14t:** deep research surfaced that our entire compute-path dep chain currently has no `cp314t` wheels and is explicitly not free-thread-safe:

- **pypdfium2** (5.7.x) ships only `py3-none-*` ctypes wheels. Its own docs state: *"PDFium is inherently not thread-safe. It is not allowed to call pdfium functions simultaneously across different threads, not even with different documents."* Maintainers closed the threading issue as "not planned" and recommend multiprocessing.
- **pikepdf** (10.5.x) carries the PyPI classifier `Programming Language :: Python :: Free Threading :: 1 - Unstable`. No `cp314t` wheels; transitively blocked by lxml which also ships no `cp314t`.
- **opencv-python-headless** (4.13.x) ships no `cp314t` wheels; open tracking issue `opencv/opencv#27933`.
- Importing any of the above under `python3.14t` triggers CPython's safety valve and **silently re-enables the GIL process-wide**, leaving us with a 5–10% single-thread regression and zero parallelism benefit.

**Our parallelism model:**
- Page-level parallelism uses `multiprocessing.ProcessPoolExecutor`, sized to `sqrt(vCPU)` as a starting point (matches OCRmyPDF's empirically-validated rule for Tesseract).
- A process-global `threading.Lock` wraps every pypdfium2 call inside a single process (pdfium is not safe even across different documents).
- Input PDFs are split via qpdf `--split-pages` to per-page work units, shipped to workers, rejoined via qpdf `--empty --pages` (structural splice, no re-encode).

**OCR pool lifetime and cleanup.** Within `_process_single_page`, a `ThreadPoolExecutor` for OCR calls is constructed conditionally (only when parallelism is needed) and managed inside a `contextlib.ExitStack`. On the happy path the stack is closed normally; on any exception path the `ExitStack.__exit__` call includes `cancel_futures=True`, preventing wedged Tesseract subprocesses from keeping the pool alive after an error or timeout. Pytesseract itself receives a `timeout=` argument (sourced from `per_page_timeout_seconds` in `CompressOptions`) so an unresponsive Tesseract subprocess is killed rather than waiting indefinitely for the pool to drain.

**Revisit for v1.1:** when `cp314t` wheels are available for pypdfium2, pikepdf (+ lxml), opencv, and when pdfium declares thread-safety (unlikely near-term per upstream statements). Realistic earliest: Q3 2026.

**Stack notes:**
- Every listed Python-side dep has 3.14 (GIL) wheels today.
- Tail-call interpreter and error-message improvements in 3.14 still apply to the GIL build.
- The team's other services are Python-heavy; reusing tooling and CI patterns reduces total surface area.

Alternatives considered and rejected:
- **Rust/Go rewrite** — enormous scope, no existing MRC library, reinvents years of PDF edge-case handling.
- **Python 3.12** — two-year-old, no reason to start a new project there.
- **Node/TypeScript** — no credible OCR, no credible JBIG2, no credible PDF-manipulation ecosystem.

## 4. Pipeline: Triage → Input-Policy Gate → Sanitize → Recompress (or Image Export)

Every PDF — regardless of origin — flows through the phases below in order. The phases are independently testable and independently sandboxable.

### 4.1 Triage

Goals: classify the input, decide whether to proceed, collect metadata for downstream decisions. Cheap operations only; never decodes image streams.

Operations:
- Structural scan via pikepdf (uses libqpdf; tolerates corrupt xrefs, surfaces repair warnings)
- pdfid-style object walker — enumerates dangerous keys (`/JavaScript`, `/JS`, `/OpenAction`, `/AA`, `/Launch`, `/EmbeddedFiles`, `/RichMedia`, `/GoToR`)
- Encryption detection (user password, owner password, certificate)
- Signature detection (`/AcroForm` `/SigFlags`, `/Sig` fields)
- Filter chain enumeration (flags already-JBIG2, DCT, CCITT, LZW content)
- Page-level metadata: count, `/MediaBox`, `/Rotate`, OCGs, tagging (`/StructTreeRoot`, `/MarkInfo`)
- PDF/A conformance detection (XMP `pdfaid:part`)

Output: a `TriageReport` structure consumed by the input-policy gate and Sanitize. Classifies into one of: `proceed`, `refuse`, `pass-through`, `require-password`.

### 4.1a Input-policy gate

Immediately after Triage and before any destructive operation, `_enforce_input_policy(tri, options, input_data)` in `hankpdf/__init__.py` is the single decision point for all early-exit conditions. This avoids scattered refusal logic and ensures both `compress()` and the CLI image-export path enforce the same rules.

Conditions that cause immediate exit here:
- Encrypted (user-password, certificate, or owner-restriction) — raises `EncryptedPdfError`
- Digitally signed without explicit opt-in — raises `SignedPdfError`
- Certified PDF (`/DocMDP` present) — raises `CertifiedPdfError`
- Input exceeds the configured size ceiling — raises `InputTooLargeError`
- Input is below `min_input_mb` — returns with `status="passed_through"` (see §4.5 on passthrough semantics)

### 4.1b Per-page MRC gate

Immediately after the input-policy gate and before Sanitize/Recompress, every page is scored once for MRC-worthiness via `score_pages_for_mrc(pdf_bytes, *, password, min_image_byte_fraction)` in `hankpdf/engine/page_classifier.py`. The gate is a cheap pre-filter — no decode, no render, just a pikepdf walk of each page's `/Resources/XObject` dict.

- **Signal**: `image_xobject_bytes / page_byte_budget` per page, where the numerator is the sum of `/Length` for every `/XObject /Image` stream and the denominator is `len(/Contents) + sum(referenced_xobject_lengths)` (image + form XObjects, floored at 1 byte). Native-export PDFs (PowerPoint/Word) sit at 0–15%; scan-derived PDFs sit at 70–95%.
- **Threshold**: `--per-page-min-image-fraction` (CLI), `CompressOptions.min_image_byte_fraction` (API). Default `0.30`. Pages at or above the threshold are MRC-worthy; pages below are emitted **verbatim** in the worker fast-path (1-page slice copied from the input with a sentinel `PageVerdict`, no rasterize, no Tesseract).
- **Whole-doc shortcut**: when **no** page meets the threshold, `compress()` skips Sanitize/Recompress entirely and returns the input bytes unchanged with `status="passed_through"` and warning code `passthrough-no-image-content`. This is distinct from the partial-passthrough case where some pages are verbatim and others are MRC'd; partial passthrough still produces a recompressed output and records the verbatim page indices in `CompressReport.pages_skipped_verbatim` (0-indexed).
- **Disable conditions**: the gate is bypassed (every page forced through full MRC) when `--re-ocr`, `--strip-text-layer`, `--legal-mode`, or `--verify` (i.e. `skip_verify=False`) is set, or when `--per-page-min-image-fraction 0` is passed. `--verify` disables the gate because a verbatim page would feed a synthetic `PageVerdict` into `_VerifierAggregator` and pollute the aggregate ssim/lev/digit metrics. A defensive assert in the worker fast-path traps any future regression that lets `skip_verify=False` leak into a verbatim slice.
- **Conservative biases**: nested Form XObjects are not recursively walked (image bytes hidden inside a Form sub-resource don't count toward the numerator), and parent-inherited `/Resources` from the `/Pages` tree are not consulted (only the page's direct `/Resources/XObject` dict). Both biases under-count image bytes, pushing borderline pages toward MRC — the safe direction for a pre-filter.

See SPEC.md §4.1b for the precise behavior contract.

### 4.2 Sanitize

Goals: strip hostile or unneeded content, pre-repair structural issues, normalize to a canonical form that Recompress can operate on safely.

Operations:
- Strip `/JavaScript`, `/OpenAction`, `/AA`, `/Launch`, `/EmbeddedFiles`, `/RichMedia` — removal recorded in sidecar manifest, never in output XMP (see §7 on provenance).
- qpdf-level repair if xref was corrupt.
- Delinearize (linearization is added back after Recompress).
- Decrypt in-process if user-supplied password (password never on argv, never logged, zeroed on drop).
- Passthrough policy for signed PDFs, certificate-encrypted PDFs, and already-optimized PDFs (see §6).

Output: a "clean" in-memory `pikepdf.Pdf` object ready for page-level work.

### 4.3 Recompress (or image-export alternate exit)

If `--output-format jpeg|png|webp` is specified, the pipeline exits Sanitize and **bypasses the MRC pipeline entirely**. Pages are rasterized via pdfium and encoded to the requested image format by Pillow (libjpeg-turbo for JPEG, libpng+zlib for PNG, libwebp for WebP). The streaming generator `iter_pages_as_images` yields encoded page bytes one at a time; the eager wrapper `render_pages_as_images` materializes all pages into a list. Both live in `hankpdf/engine/image_export.py`. The input-policy gate (§4.1a) still applies before this path; the MRC verifier does not.

For the standard PDF-in, PDF-out case:

Goals: achieve the target compression ratio while preserving content and searchability. The algorithm follows ITU-T T.44 (Mixed Raster Content) and draws on the DjVu foreground/background decomposition literature (Léon Bottou et al., AT&T Labs, 1998–2001). See [KNOWLEDGE.md §2](KNOWLEDGE.md) for the full technical reference.

**Not all pages get MRC.** The research showed that MRC overhead (three image XObjects + SMask dict + transparency group) can exceed gains on text-only and photo-only pages, and re-MRC'ing an already-MRC'd input inflates output. Each page is classified before compression.

#### 4.3.1 Per-page strategy selector

Before compression, classify each page:

| Class | Detection | Strategy |
|---|---|---|
| **Already-optimized** | Input page's image XObjects include existing `/JBIG2Decode` + `/SMask` pattern, or embedded JPEG at DCT quality < 40 | Pass-through — copy page unchanged |
| **Text-only** | Mask coverage >95%, grayscale background variance <5% | Single `/JBIG2Decode` page image; no SMask, no background layer |
| **Photo-only** | Mask coverage <5%, uniform high-frequency background | Single `/JPXDecode` (JPEG2000) page image; no foreground/mask split |
| **Mixed** | Default | Full MRC pipeline (§4.3.3) |

#### 4.3.2 CMYK pre-pass (applied to all classes)

pdfium rasterizes CMYK content via a naive non-ICC-managed CMYK→sRGB lookup (`core/fxge/dib/cfx_cmyk_to_srgb.cpp`), producing desaturated colors. Public API emits only RGB/Gray bitmaps — no CMYK output path.

**For pages whose input `/ColorSpace` is `/DeviceCMYK` or an ICC CMYK profile**, we do a managed conversion to sRGB via **littlecms** (lcms2, MIT-style license) before rasterization. This preserves color fidelity on medical/legal scans that arrive as CMYK.

#### 4.3.3 MRC per-page pipeline (mixed pages)

1. **Rasterize** via pdfium at working DPI (default 300). Caller holds the process-global pdfium lock.
2. **Small-print check** (pre-OCR) — histogram connected-component pixel heights. If p50 x-height < 12 px, upscale 2× (OpenCV `INTER_CUBIC`) OR route to "full-JPEG safe mode" (skip MRC, encode page as single JPEG at elevated quality). Prevents silent drop of fine-print text below the 8 px threshold where Tesseract stops returning boxes.
3. **Detect text regions** via Tesseract hOCR word boxes + OpenCV adaptive threshold (Sauvola/Niblack) + connected-components for non-text line art. Union, morphological close with 3×3 or 5×5 kernel.
4. **Build 1-bit mask** matching foreground dimensions exactly (not resampled — Quartz/Preview drops SMasks with dimension mismatch).
5. **Extract foreground layer** — text/line-art pixels at mask-1, encoded as **lossless JBIG2 generic region coding only** (no symbol mode, no refinement flag — avoids both Xerox-6/8 substitution and documented Acrobat crashes).
6. **Extract background layer** — mask-0 pixels inpainted to fill text regions, downsampled to 100–150 DPI (default 150), encoded as JPEG2000 (OpenJPEG) OR JPEG. **JPEG uses chroma subsampling 4:4:4 (`subsampling=0`) not Pillow's default 4:2:0** — 4:2:0 smears colored text in the background layer (stamps, highlighter, colored ink).
7. **Compose PDF page** — three image XObjects (mask, foreground, background) inside a Form XObject using standard PDF `/SMask` construct (PDF 1.4+). All image XObjects carry explicit `/ColorSpace [/ICCBased <sRGB>]` to fix Quartz-vs-pdfium color drift.
8. **Embed invisible OCR text layer** — Tesseract output positioned from hOCR word boxes; text rendering mode 3 (invisible). Preserves copy-paste and search.
9. **Assemble** — pages stitched via qpdf `--empty --pages` (structural splice, no re-encode). Deterministic `/ID` via qpdf `--deterministic-id`.
10. **Output intent**: single sRGB `/OutputIntent` on the document (enables PDF/A-2u conformance).

#### 4.3.4 Legal / archival codec profile

Optional flag (`--legal-mode` on the CLI, `legal_codec_profile=True` on the API) forces **CCITT Group 4 instead of JBIG2** on the 1-bit layer. Reason: German BSI TR-03138 forbids JBIG2 for legally-compliant replacement scanning, and US NARA's PDF/A acceptable-codec list omits JBIG2. Paired with PDF/A-2u output target. Gives up ~20% compression on the 1-bit layer but unblocks gov / legal / archival workflows.

#### 4.3.5 Expected ratios (unchanged)

From published benchmarks on this exact algorithm:
- Color scans: 5–20×
- Mostly-text monochrome pages: 20–100×
- Clean text-only legal/medical: 200×+

Ratio comes primarily from aggressive background downsampling + color→mono detection, not from any single codec. JBIG2 contributes 20–50% of the mono gain; the rest is downsampling.

### 4.4 Passthrough floors

`compress()` checks three passthrough conditions across the pipeline; any one returning `status="passed_through"` short-circuits everything downstream:

- **`min_input_mb`** (pre-Sanitize): if the raw input is smaller than this floor, no compression is attempted at all; the input bytes are returned unchanged. Checked in the input-policy gate (§4.1a).
- **`min_image_byte_fraction`** (pre-Sanitize): if no page meets the per-page MRC gate's image-byte threshold, the whole document is returned unchanged with warning `passthrough-no-image-content`. Checked in the per-page MRC gate (§4.1b).
- **`min_ratio`** (post-Recompress): if the achieved compression ratio falls below this floor (default `1.5×`), the input bytes are returned unchanged. This avoids delivering a "compressed" output that is actually larger or only marginally smaller than the original.

All three conditions are declared in `CompressOptions`. Callers can detect passthrough via `CompressReport.status`.

### 4.5 Chunked output

When `--max-output-mb` is set, the output is split into multiple files rather than one potentially large PDF. The greedy per-page packer in `hankpdf/engine/chunking.py` accumulates pages into a chunk until adding the next page would exceed the byte budget, then flushes and starts a new chunk.

Output filenames follow the pattern `{base}_{NNN}{ext}` where `NNN` is 1-indexed and zero-padded to the number of digits needed for the total chunk count (e.g. `report_01.pdf`, `report_02.pdf`). This applies to both the MRC path and the image-export path.

### 4.6 Three-layer timeout model

All timeout durations are configured via `CompressOptions` and applied consistently across both serial and parallel page processing:

| Layer | Mechanism | Exception raised |
|---|---|---|
| **Per-OCR-call** | pytesseract `timeout=` kwarg (sourced from `per_page_timeout_seconds`) | `OcrTimeoutError` |
| **Per-page watchdog** | `future.result(timeout=per_page_timeout_seconds)` in serial mode; `as_completed(timeout=...)` in parallel mode | `PerPageTimeoutError` |
| **Total wall-clock watchdog** | `_check_total_timeout` called at phase boundaries (after Triage, after each page, after Recompress) | `TotalTimeoutError` |

All three exception types are subclasses of `CompressError`, so callers that catch the base class handle all timeout cases. The per-OCR timeout prevents wedged Tesseract subprocesses from stalling the thread pool indefinitely; when a `PerPageTimeoutError` or `TotalTimeoutError` fires during parallel processing, the `ExitStack`-managed `ThreadPoolExecutor` is torn down with `cancel_futures=True` so no zombie futures outlive the exception handler (see §5 on ExitStack OCR pool lifetime).

## 5. Content-preservation verifier (mandatory gate)

After Recompress, before returning the output, every page is verified against the input. Any page failing triggers `ContentDriftError` — the whole job aborts, nothing is silently shipped.

**Non-determinism caveat.** Tesseract's LSTM is not bit-deterministic across hosts/platforms (float32 accumulation order varies by BLAS vendor + hardware). Verifier compares OCR text generated on the same host for both input-rasterization and output-rasterization to sidestep cross-host drift. The input OCR result is cached keyed by SHA-256 of the rasterized input page image; same input page reused → same text.

Checks (all must pass):

| Check | Metric | Threshold | Rationale |
|---|---|---|---|
| **Digit-multiset exact match** | Regex-extract digit runs (including decimals + unit suffixes `mg`/`mcg`/`mL`/`IU`) from input and output OCR text; multiset equality | Exact | Replaces "numeric confidence gate." Tesseract's per-word confidence is miscalibrated and the same number applies to every char in a word, so confidence-based gating on digits is fiction. Exact-match on digit runs is the only defensible test for "we didn't drop a decimal." |
| **Reading-order-insensitive Levenshtein** | Bag-of-lines Levenshtein (split to lines, best-match pair, sum distances) | ≤ 2% | Multi-column medical forms reparse column order differently between input and output OCR passes. Raw sequence Levenshtein inflates without content change; bag-of-lines neutralizes the false signal. |
| **Raw Levenshtein** | Sequence Levenshtein on page OCR text | ≤ 5% | Secondary; loose threshold because of the reading-order issue above. Both Levenshteins must pass. |
| **Global SSIM** | scikit-image SSIM on full-page grayscale render | ≥ 0.92 for MIXED and TEXT_ONLY; ≥ 0.50 for PHOTO_ONLY (q=45 at target_dpi is legitimately noisy vs. a q=95 input) | Catches gross structural drift. |
| **Tile-level SSIM** | Min SSIM over 50×50-pixel tiles | ≥ 0.85 (standard) / ≥ 0.88 (safe mode), **MIXED pages only** — TEXT_ONLY and PHOTO_ONLY skip tile SSIM because JBIG2-binary and downsampled JPEG outputs always score near zero tile-wise against anti-aliased input | Catches small-region drift that global SSIM averages away — critical for medical (smeared catheter tip, lost decimal point). |
| **Channel parity** | Fraction of pixels with RGB spread > `CHANNEL_SPREAD_COLOR_TOLERANCE` (15) on input vs. output; also connected-component ≥ 200 px | input has color → output must have color | Catches silent color loss that SSIM-on-L cannot see (Phase 2b). Same constant is imported by `foreground.is_effectively_monochrome` so a color region can't route to TEXT_ONLY and still pass the verifier. |
| **Structural audit** | Page count, `/Annot` count, form fields, signature presence, attachments | Exact match or explicitly-logged strip | Structure preservation. |

**Safe mode** tightens thresholds (raw Levenshtein ≤ 2%, tile SSIM ≥ 0.88) and enables the stricter digit-multiset exact-match gate by default. On any tile SSIM below the safe threshold on a MIXED page, instead of returning a pass, the engine returns `ContentDriftError` with exit 21 — the caller (user or script) decides whether to accept the original or retry at different settings. Intended for content classes where stricter preservation matters (clinical records, legal documents, anything where a silent digit swap is unacceptable).

**Verifier honesty / skipped path.** `VerifierResult.status` is not a boolean pass/fail — it is an enum including `"skipped"`. When `skip_verify=True` is passed (e.g. via `--skip-verify` on the CLI), `_VerifierAggregator.skipped_result()` emits a fail-closed sentinel: `ssim=0`, `lev=1`, `structural_match=False`. This ensures any downstream consumer treating verifier metrics as a quality signal will see a conservative (pessimistic) result, not a falsely clean one. The CLI prints a stderr banner when the verifier was skipped or failed, and the `ProgressEvent` for the verifier step carries `verifier="skipped"` so live tqdm progress bars do not contradict the final report.

**Pre-verifier content extraction**: the verifier runs OCR regardless of whether the user requested OCR in the output, because OCR diff is the primary signal. The OCR pass is disposable.

**Invalid UTF-8 handling**: Tesseract occasionally emits invalid UTF-8 bytes; decode with `errors='replace'` both sides to maintain symmetry and avoid exceptions.

**Decompression-bomb guard** (runs before any SSIM): `PIL.Image.MAX_IMAGE_PIXELS` is set at import time by `hankpdf/_pillow_hardening.py`. The cap value is declared once in `hankpdf/_limits.py` (`MAX_BOMB_PIXELS`) and shared between Pillow's import-time cap and image-export's pre-allocation pixel budget check, so the two limits cannot diverge. Decode is wrapped in try/except; Pillow's `DecompressionBombError` is translated to our typed `DecompressionBombError` subclass so the CLI can route to its dedicated exit code (`EXIT_DECOMPRESSION_BOMB=16`). Structured counter: `rejected:decompression_bomb`.

## 6. Weird-PDF handling matrix

Each class gets an explicit policy. The full detection and response table lives in [SPEC.md §4](SPEC.md). Summary of policies:

- **Refuse + pass-through** (return original, no recompression): certificate-encrypted, digitally signed (without explicit opt-in), already-heavily-lossy (JPEG q<40).
- **Require input from user**: user-password-encrypted (prompt for password).
- **Proceed with strip + log**: contains `/JavaScript`, `/OpenAction`, `/EmbeddedFiles`, etc.
- **Proceed with pass-through of specific streams**: already-JBIG2 content (never re-decode JBIG2 outside sandbox — ForcedEntry / CVE-2021-30860 attack class).
- **Proceed with preservation**: tagged PDFs (preserve `/StructTreeRoot`, `/MarkInfo`), CMYK color (preserve, don't convert to sRGB), unusual rotation metadata, layered OCGs, linearized PDFs.
- **Refuse as malicious**: sandbox resource-cap-exceeded (JBIG2 bomb, xref loop, recursive forms, page size > 200"×200"), input > configured hard max.

## 7. Provenance: sidecar manifest, not XMP

Every output is accompanied by a `<input-basename>.hankpdf.json` sidecar containing:

- Engine version
- Compression ratio
- Input / output SHA-256
- List of strips (`/JavaScript`, `/EmbeddedFiles`, etc.)
- Verifier metric values
- Timestamp
- Per-page warnings

We do **not** write engine-version or strip-records into the output PDF's XMP metadata because:
- XMP survives forwarding and forensic analysis — leaking "processed by hankpdf vX" is an OSINT signal attackers can use.
- Writing XMP modifies the PDF byte range, invalidating any digital signature — even the "pass-through signed PDFs" case would break if we also wrote XMP.

## 8. Sandboxing

Every invocation of **Triage, Sanitize, AND Recompress** runs inside a process-level sandbox. Research surfaced that even Triage can segfault (pikepdf #568: `qpdf --check` passes, pikepdf still crashes on some malformed inputs with no diagnostic). A crashing Triage before compression is still a worker crash. All three phases get isolated children.

### 8.1 All platforms — process-level isolation

Engine work runs in a child subprocess so a malformed/hostile PDF can't kill the user's shell or whatever script is invoking HankPDF.

- **Resource caps** (per child): `RLIMIT_AS` 2 GB default, `RLIMIT_CPU` 120 s per page, wall-clock watchdog 20 min per PDF.
- **Unix (Linux, macOS)**: `resource.setrlimit(...)` on the child at startup.
- **Windows**: Job Objects with memory and CPU limits and break-away-from-job disabled (via `pywin32` or equivalent).
- **No network is opened by the engine** — the user's host firewall is the outer backstop; we don't need network isolation because we never try.

The per-page and total wall-clock timeout layers (§4.6) complement the OS-level resource caps: `RLIMIT_CPU` is a coarse safety net for runaway processes; the Python-level timeouts provide deterministic, exception-carrying interruption that the engine can recover from gracefully.

### 8.2 Docker image — defense-in-depth for shared-runner users

When HankPDF runs in our published Docker image, the image ships with:
- Non-root user (`hankpdf`, UID 1000).
- Read-only rootfs (except `/tmp`).
- Baked seccomp profile allowlisting only the syscalls our native deps need (no `socket`, no `execve` post-setup, no `ptrace`, no `unshare`, no `clone3`).
- No network by default when users follow our docs (`--network none`).

Users who want stronger isolation (shared multi-tenant CI runners, hostile PDFs from unknown sources) can run our image under `gVisor runsc` or `Firecracker` themselves — we don't bundle those, but the image is known to work under both based on its seccomp-friendly footprint. That's a user operational choice, not a HankPDF-provided feature.

## 9. Deployment shapes

Both shapes run the engine **locally** on the user's machine. No network, no server, no telemetry. Anything the user does with the output — uploading to their own pipeline, emailing, archiving — is their business, not ours.

### 9.1 The two install targets

Same engine, two ways to get it onto a machine:

- **Python package** (`pip install hankpdf`) — installs the `hankpdf` console script plus the importable Python API (`from hankpdf import compress, CompressOptions`). Primary target. Users install the non-Python native deps (Tesseract, jbig2enc) via their system package manager — one line on every major OS, documented in `docs/INSTALL.md`. Wheel is published to PyPI.
- **Docker image** (`ghcr.io/hank-ai/hankpdf:X.Y`) — ~300 MB multi-arch image with all native deps (pdfium, Tesseract, jbig2enc, OpenJPEG, qpdf) baked in. For users who don't want to manage native deps on the host; for CI pipelines; for SFTP upload wrappers; for sealed execution environments. Non-root user, writable `/tmp`, read-only rootfs friendly.

Contract details — flags, exit codes, JSON report schema — in [SPEC.md §2](SPEC.md).

### 9.2 What we are NOT building

To keep scope honest:
- **No standalone binary.** No PyInstaller per-platform executables, no code-signing, no Apple Developer ID, no Windows Authenticode, no notarization, no SmartScreen reputation game. Users who need a binary on a system without Python or Docker can PyInstaller-build one themselves from source in a few minutes — but we don't ship that as a product artifact.
- **No GUI.** No drag-drop app, no Tauri shell, no desktop installer framework.
- **No hosted service.** No SaaS, no API endpoint, no tenant system, no account system, no authentication, no sign-in flow.
- **No telemetry.** No analytics, crash reporting, usage tracking, or phone-home. `--doctor` emits diagnostics to stdout only when the user runs it.
- **No integration with any specific upstream.** HankPDF doesn't know or care where the input PDF came from or where the output is going.
- **No auto-update.** Python users upgrade via `pip install -U hankpdf`. Docker users repin a tag. That's the whole mechanism.

Users who want to wrap HankPDF inside their own ingestion pipeline, desktop app, or distribution can do so trivially via the CLI or Python API. That's their infrastructure, not ours.

## 10. Configuration

The single engine is exposed through a unified `compress(bytes, CompressOptions) -> (bytes, CompressReport)` interface. CLI flags and Python API calls all produce the same `CompressOptions` struct. See [SPEC.md §1.1](SPEC.md).

`compress()` also accepts an optional `progress_callback: Callable[[ProgressEvent], None]` argument. The CLI wires this to tqdm progress bars for both the MRC and image-export paths. `ProgressEvent` carries a `verifier` field that distinguishes `"skipped"` when `skip_verify=True` is set, so the live bar never reports false verifier progress (see §5 on verifier honesty).

`CompressReport.schema_version` is currently **4**. v4 (additive over v3) adds `pages_skipped_verbatim: tuple[int, ...]` and the per-page-gate warning codes (`passthrough-no-image-content`, `pages-skipped-verbatim-N`). A migration note for consumers reading earlier-version reports is in SPEC §11.1.

Configuration precedence: command-line flag > environment variable > config file > built-in default.

## 11. Security posture

HankPDF runs on the user's machine. We are not a Business Associate, HIPAA covered entity, or data processor — the user's PDF never leaves their device by our action. But a compression tool that chokes on hostile input or leaks content via side-channels still creates real risk for the user. The posture below covers what we do to avoid being the weak link in *their* workflow.

- **No network calls.** Engine makes zero outbound network requests during compression. The only network activity involved with HankPDF at all is whatever the user does with the output (we don't initiate any).
- **No persistent storage outside user-requested output.** Input is read, output is written to the path the user specified, sidecar manifest (optional) sits next to output. `TemporaryDirectory()` context manager cleans intermediate files even on SIGKILL.
- **Atomic output writes.** Every user-facing output file (compressed PDF, image-export frames, chunked outputs) is written via `hankpdf/utils/atomic.py::_atomic_write_bytes`, which writes to a `.partial` sibling first and then calls `Path.replace()`. On POSIX, `rename(2)` is atomic, so a partially-written output file is never observable to the user — interrupted runs do not corrupt the destination path.
- **Passwords**: never on argv (subprocess argv is `ps`-visible). Passed via `--password-file` or `HANKPDF_PASSWORD` env var. Held in a bytes buffer, zeroed on exit. `PR_SET_DUMPABLE=0` on Linux to block password leak via core dumps.
- **Logs** stay on the user's machine. Every `[hankpdf]` stderr line carries a stable `[W-*]` (warning) or `[E-*]` (error) code from `hankpdf/cli/warning_codes.py`, plus a SHA-redacted input filename prefix. Ten warning codes and thirteen error codes are documented in SPEC §8.5.1. Stable codes let scripts and log aggregators key on codes rather than free-text messages; the SHA prefix lets users correlate warnings to inputs without exposing the raw filename. OCR text is never logged.
- **Sandboxing** of the engine subprocess (see §8) protects the user's machine from hostile PDFs — not us from their content. If a malformed PDF would crash or RCE a parser, the sandbox contains it.
- **CVE hygiene**: tight dependency pins (qpdf ≥11.6.3, OpenJPEG ≥2.5.4, pdfium tracked weekly). See [KNOWLEDGE.md §7](KNOWLEDGE.md).
- **Release artifact integrity**: PyPI upload via trusted publishing (GitHub OIDC, no long-lived tokens); GHCR image via GitHub OIDC; GitHub Releases with SHA-256 checksums in release notes. Users can verify downloaded artifacts against published checksums. No separate code-signing infrastructure because we don't ship platform-native binaries.

**If a user embeds HankPDF inside their own HIPAA-covered pipeline**, that pipeline's compliance is the user's responsibility. HankPDF makes that easier by never reaching off-machine, but we don't carry a BAA because there's no service to carry one against.

### 11.1 Render-size protection (two-tier)

Two independent caps protect against decompression-bomb PDFs that would allocate billions of pixels:

1. **Pre-allocation pixel-count guard.** `hankpdf.engine._render_safety.check_render_size(width_pt, height_pt, dpi)` is called BEFORE pdfium allocates the bitmap. It computes the target pixel dimensions from the page geometry and refuses with `hankpdf.exceptions.DecompressionBombError` if the product would exceed `hankpdf._limits.MAX_BOMB_PIXELS` (~715 Mpx, sized so an RGB raster fits in 2 GiB). Both the compress path (`rasterize.rasterize_page`) and the image-export path (`image_export._iter_pages_impl`) call this helper. Tests in `tests/unit/engine/test_render_safety.py`.
2. **Post-decode Pillow guard.** `PIL.Image.MAX_IMAGE_PIXELS` is set to the SAME value by `hankpdf._pillow_hardening.ensure_capped()` so any image opened through Pillow (e.g., a per-page raster being re-encoded) hits the same ceiling. Pillow raises `PIL.Image.DecompressionBombError`, which our engine modules re-raise as our typed `DecompressionBombError` for consistent CLI exit-code mapping (`EXIT_DECOMPRESSION_BOMB=16`).

Both caps share `hankpdf._limits.MAX_BOMB_PIXELS` as the canonical numeric value — the `tests/unit/test_pillow_hardening.py` suite asserts they don't drift apart.

Callers that knowingly need a higher ceiling (e.g., a future per-page render CLI for engineering drawings) can pass `max_pixels=N` to `check_render_size` for a per-call override; there is intentionally no override for the Pillow cap (it's a global SECURITY boundary, not a tuning knob).

**Operational note.** The Pillow cap (`PIL.Image.MAX_IMAGE_PIXELS`) is a process-global value installed at import time. There is currently no public API to relax it for a specific job — the per-call `max_pixels` override on `check_render_size` only affects our pre-allocation guard, not Pillow's. Workloads needing pages larger than ~715 Mpx require either an external pre-rasterization step or a forked build that adjusts `_limits.MAX_BOMB_PIXELS`. We may add a context-manager API in a future major version if the use-case shows up.

## 12. What we're explicitly not building

To keep scope honest:

- **Not a generic PDF editor.** No page reordering, no redaction UI, no form filling.
- **Not an OCR service.** We do OCR as a means to an end (searchability + verifier). Customers who want OCR-as-a-product should use Tesseract directly or commercial alternatives.
- **Not a PDF/A conversion tool.** We aim for PDF/A-2u output where input permits, but we don't offer PDF/A-3, PDF/A-1b mode switches, or explicit archive-grade profiles.
- **Not a MIME/OOXML compressor.** Only PDF input.
- **Not an image ingestion tool.** We don't accept TIFFs, JPEGs, or image folders as *input*. Wrap them in a PDF first. (PDF-to-image *output* is supported via `--output-format jpeg|png|webp`.)

## 13. Decisions and open questions

### Decided
- **Product brand**: HankPDF.
- **Repo/package name**: `hankpdf` (internal); CLI binary and all user-visible strings use `hankpdf` / HankPDF.
- **Safe mode** (formerly working title "medical mode"): explicit opt-in via `--mode safe` flag or `mode="safe"` option. Default is `standard`.
- **Test corpus strategy**: URL-referenced. Files live in an S3 bucket we control; the repo holds a manifest (filename + URL + SHA-256). CI caches; developers run `scripts/fetch_corpus.py` on demand. No Git LFS, no committed binaries.
- **Real-corpus validation**: sample of PDFs collected from the open internet (Internet Archive, govinfo.gov, public-domain book scans, USPTO patents) plus synthetic edge-case fixtures we generate ourselves. No customer data, no DPAs, no BAAs — nothing to carry.

### Also decided
- **CPython 3.14 standard GIL for v1 (REVERSED from earlier free-threaded decision).** Research found that our full compute-path dep chain (pypdfium2, pikepdf, opencv-python, lxml) has no `cp314t` wheels in April 2026 and pypdfium2 is explicitly documented as not thread-safe by its maintainers. Running under `python3.14t` silently re-enables the GIL and delivers no parallelism benefit. Parallelism instead uses `multiprocessing.ProcessPoolExecutor`. Revisit `python3.14t` for v1.1 when ecosystem catches up.
- **Corpus seed**: loose — pull a handful of oversized public PDFs from the open internet (Internet Archive book scans, govinfo.gov large reports, USPTO patents with diagrams). Keep it simple; grow it as we hit edge cases.
- **Repo visibility**: private for now. Go public when ready.
- **License**: Apache-2.0. Permissive (others may use, modify, redistribute) but requires attribution and NOTICE preservation — matches the "ok for others to use but must list us as author" requirement.
- **DCO vs CLA**: moot while private. Revisit at the point of going public.
- **Three-layer timeout model implemented** (§4.6). Per-OCR, per-page, and total wall-clock timeouts are all live; each raises a distinct typed exception. This replaces earlier designs that only had OS-level `RLIMIT_CPU` as a backstop.
- **Passthrough floors implemented** (§4.4). `min_input_mb` and `min_ratio` are both in `CompressOptions` and enforced in production. Default `min_ratio=1.5`.
- **Input-policy gate centralized** (§4.1a). All early-exit refusal logic consolidated into `_enforce_input_policy`; the gate is called identically from `compress()` and the CLI image-export path, eliminating dual-maintenance risk.
- **Atomic writes everywhere** (§11). All output paths use `_atomic_write_bytes`; no half-written files observable to user or downstream scripts.
- **Stable warning/error codes on all stderr output** (§11). Ten `[W-*]` and thirteen `[E-*]` codes in `hankpdf/cli/warning_codes.py`; scripts can key on codes rather than parsing message text.
- **Pillow decompression-bomb cap centralized in `_limits.py`**. `MAX_BOMB_PIXELS` is the single source shared between `_pillow_hardening.py` and image-export's pre-allocation budget. The two limits cannot diverge silently.
