# HankPDF — Functional Specification

Precise behaviors, contracts, and data formats. For the *why* behind these choices see [ARCHITECTURE.md](ARCHITECTURE.md). For algorithm and codec background see [KNOWLEDGE.md](KNOWLEDGE.md).

## 1. Python API (source of truth)

All other surfaces (CLI, Docker entrypoint) call this API internally.

### 1.1 Types

```python
@dataclass(frozen=True)
class CompressOptions:
    # Engine selection
    engine: Literal["mrc", "downsample-only"] = "mrc"
    bg_codec: Literal["jpeg", "jpeg2000"] = "jpeg"  # jpeg2000 is ~10-20% smaller on paper textures; +~1-2s/page at 300 DPI (demoted to jpeg in fast mode)

    # Quality / ratio knobs
    target_bg_dpi: int = 150            # background layer DPI after downsample
    target_color_quality: int = 55      # JPEG / JPEG2000 quality 0-100 for bg (calibrated 0-100, NOT PSNR dB)
    bg_chroma_subsampling: Literal["4:4:4", "4:2:2", "4:2:0"] = "4:4:4"  # 4:4:4 avoids smearing colored text in bg
    force_monochrome: bool = False      # skip color detection, treat all as B&W
    mode: Literal["fast", "standard", "safe"] = "standard"

    # Archival / legal profile
    legal_codec_profile: str | None = None  # reserved: "ccitt-g4" raises NotImplementedError until a later phase
    target_pdf_a: bool = False          # target PDF/A-2u output (stricter codec/color-space constraints)

    # OCR behavior — off by default; --ocr opts in (adds ~5 s/page to embed a searchable text layer)
    ocr: bool = False                   # write an embedded OCR text layer to the output PDF
    ocr_language: str = "eng"           # Tesseract language code(s), e.g. "eng+spa"

    # Safety / behavior gates
    allow_signed_invalidation: bool = False     # opt-in for signed PDFs (invalidates signature)
    allow_certified_invalidation: bool = False  # stricter opt-in for /Perms/DocMDP certifying signatures
    allow_embedded_files: bool = False          # keep /EmbeddedFiles instead of stripping
    accept_drift: bool = False                  # if True, verifier-flagged drift → warning instead of abort
    skip_verify: bool = True                    # skip content-drift verifier (default True; --verify turns it on)
    password: str | None = None                 # for encrypted inputs

    # Thresholds
    min_input_mb: float = 0.0           # below this, skip compression and pass through
    min_ratio: float = 1.5              # if achieved ratio < this, return original
    min_image_byte_fraction: float = 0.30  # per-page MRC gate; pages below this fraction are emitted verbatim. See §4.1b.

    # Limits
    max_pages: int | None = None        # refuse inputs over this page count
    max_input_mb: float = 2000.0        # refuse inputs over this size
    per_page_timeout_seconds: int = 120
    total_timeout_seconds: int = 1200
    photo_target_dpi: int = 200         # DPI for PHOTO_ONLY pages (higher than bg to preserve micro-detail)

    # Concurrency — each worker gets its own single-page slice (memory scales with workers × 1 page, not workers × whole source)
    max_workers: int = 0                # 0 = auto (cpu_count-2, ≥1); 1 = serial; N>1 = exactly N workers
    max_worker_memory_mb: int | None = None  # per-worker memory cap override (MB).
                                             # None = auto via _compute_worker_mem_cap:
                                             #   min(max(8 GB, 16 × input_size), 16 GB),
                                             # then clamped by the aggregate-envelope check
                                             #   psutil.virtual_memory().available × 0.7 / n_workers.
                                             # 0 = disable cap entirely (advanced; opts out of
                                             # both the platform setrlimit/Job Object cap and the
                                             # parent-side psutil RSS watchdog).
                                             # If the requested cap × n_workers would exceed
                                             # 70% of host RAM at startup, compress() raises
                                             # HostResourceError (exit 19, [E-HOST-RESOURCE]).

    # Signature handling
    preserve_signatures: bool = False   # if True, signed inputs are passed through verbatim
                                        # (signature stays valid). status="passed_through",
                                        # signature_state="passthrough-preserved",
                                        # warning [W-PASSTHROUGH-SIGNED] emitted.

    # Output
    emit_sidecar_manifest: bool = True
    output_pdf_version: str = "1.7"     # output PDF version target

@dataclass(frozen=True)
class CompressReport:
    status: Literal["ok", "passed_through", "refused", "drift_aborted"]
    exit_code: int                      # stable exit code; see §2.2
    input_bytes: int
    output_bytes: int
    ratio: float                        # input_bytes / output_bytes (1.0 if passthrough)
    pages: int
    wall_time_ms: int
    engine: str
    engine_version: str
    verifier: VerifierResult
    input_sha256: str
    output_sha256: str
    canonical_input_sha256: str | None  # see §5; None if canonicalization failed
    warnings: tuple[str, ...] = ()      # structured warning codes; see §8.5
    strips: tuple[str, ...] = ()        # what was stripped; see §4.4
    reason: str | None = None           # human-readable reason if refused/drift
    schema_version: int = 5             # sidecar / JSON report schema version (see §11)
    strategy_distribution: Mapping[str, int] = field(default_factory=dict)
    # strategy_distribution: per-page strategy counts — keys are
    # "text_only", "photo_only", "mixed", "already_optimized"; values are
    # page counts. Emitted by compress() for ratio post-mortems. See §8.5.
    pages_skipped_verbatim: tuple[int, ...] = ()  # 0-indexed page indices skipped by the per-page MRC gate; empty on full-pipeline runs and on whole-doc passthrough. See §4.1b.
    signature_state: Literal[
        "none",
        "passthrough-preserved",
        "invalidated-allowed",
        "certified-invalidated-allowed",
    ] = "none"
    signature_invalidated: bool = False
    worker_memory_cap_bytes: int = 0    # per-worker memory cap actually applied (bytes); 0 if uncapped
    worker_peak_rss_max_bytes: int = 0  # max observed worker RSS across the run (bytes); 0 if not measured

@dataclass(frozen=True)
class VerifierResult:
    status: Literal["pass", "fail", "skipped"]
    ocr_levenshtein: float              # worst per-page Levenshtein ratio
    ssim_global: float                  # min over pages
    ssim_min_tile: float                # min over all tiles, all pages
    digit_multiset_match: bool          # exact-match on the multiset of digits extracted from input vs output OCR
    structural_match: bool              # exact match on structural audit
    failing_pages: tuple[int, ...] = () # 1-indexed, empty on pass
    color_preserved: bool = True        # false if monochrome-mode flattened a color page (see force_monochrome)

class CompressError(Exception): ...
class EncryptedPDFError(CompressError): ...        # needs password
class SignedPDFError(CompressError): ...           # signed, needs opt-in
class CertifiedSignatureError(SignedPDFError): ... # /Perms/DocMDP — stricter opt-in required
class MaliciousPDFError(CompressError): ...        # sandbox resource cap exceeded
class ContentDriftError(CompressError): ...        # verifier failed
class OversizeError(CompressError): ...            # exceeds max_input_mb or max_pages
class DecompressionBombError(CompressError): ...   # exceeds Pillow MAX_IMAGE_PIXELS or pixel-count cap
class CorruptPDFError(CompressError): ...          # unrecoverable by pikepdf/qpdf
class EnvironmentError(CompressError): ...         # qpdf/pdfium version floor violated; see --doctor
class HostResourceError(CompressError): ...        # requested cap × n_workers exceeds 70% of host RAM (exit 19)
```

`ProgressEvent` is emitted via the optional `progress_callback` parameter on `compress()` (see §1.2). Carries no PHI — only pipeline phase, page indices, strategy names, byte counts, and ratios. The CLI drives its tqdm bar from these events; programmatic callers can log them, collect metrics, or drive their own UI.

```python
ProgressPhase = Literal[
    "triage",
    "triage_complete",
    "page_start",
    "page_done",
    "merge_start",
    "merge_complete",
    "verify_complete",
]

@dataclass(frozen=True)
class ProgressEvent:
    phase: ProgressPhase
    message: str
    current: int = 0                    # 1-indexed page number during per-page phases; 0 otherwise
    total: int = 0                      # total page count; 0 outside the per-page phase
    strategy: str | None = None         # set for page_start / page_done (e.g. "text_only", "mixed")
    ratio: float | None = None          # set for page_done: true per-page file ratio
    input_bytes: int | None = None      # set for page_done: this page's size in the input PDF
    output_bytes: int | None = None     # set for page_done: this page's size in the output PDF
    verifier_passed: bool | None = None # set for page_done: False if the per-page verifier failed
```

### 1.2 Functions

```python
def compress(
    input_data: bytes,
    options: CompressOptions | None = None,
    *,
    progress_callback: Callable[[ProgressEvent], None] | None = None,
    only_pages: set[int] | None = None,
) -> tuple[bytes, CompressReport]:
    """
    Compress a PDF. Returns (output_bytes, report).

    On success: output_bytes is the compressed PDF.
    On passthrough: output_bytes is the input unchanged.
    On refuse or drift: raises the appropriate exception.

    progress_callback: optional fn(event: ProgressEvent) -> None invoked at
    pipeline milestones (triage, per-page start/done, merge, verify). No PHI
    is emitted — see §1.1 ProgressEvent. The CLI drives tqdm from these.

    only_pages: 1-indexed page numbers to restrict processing to. Output PDF
    contains only the selected pages, in original order. Pages outside the
    set are skipped entirely (no rasterize / OCR / verify). Useful for smoke
    tests and partial re-processing.
    """

def compress_stream(
    input_stream: IO[bytes],
    output_stream: IO[bytes],
    options: CompressOptions = CompressOptions(),
) -> CompressReport:
    """
    Streaming variant. Writes to output_stream, returns report.
    Used by CLI stdin/stdout mode and the server worker.
    """

def triage(input_data: bytes) -> TriageReport:
    """
    Cheap structural scan. Never decodes image streams.
    Returns classification + detected hazards.
    """
```

## 2. CLI contract

The CLI binary is named `hankpdf`. Same binary on Windows, macOS, Linux (file extension differs).

### 2.1 Flags

```
hankpdf INPUT [-o OUTPUT] [--options...]

Positional:
  INPUT                     Path to input PDF, or "-" for stdin.

Output:
  -o, --output PATH         Path to output PDF, or "-" for stdout.
  --output-format {pdf,jpeg,png,webp}
                            Default: inferred from -o extension (.pdf, .jpg/
                            .jpeg, .png, .webp) or 'pdf' if unknown.
                            Selecting jpeg/png/webp switches to image-export
                            mode: each selected page is rendered and encoded
                            as a standalone image file (no MRC compression,
                            no verifier, no OCR). Use --pages to select a
                            subset.
  --max-output-mb FLOAT     Cap the output PDF size. If the compressed output
                            exceeds this value, split into multiple files
                            named {base}_001{ext}, {base}_002{ext}, ...
                            (zero-padded, 1-indexed) preserving page order.
                            Pad width scales to max(3, len(str(total))) so
                            1200-chunk jobs produce {base}_0001{ext} rather
                            than a mix of 3- and 4-digit names that sort-lex
                            wrong. Useful for email attachment limits. A
                            single page that's already larger than the cap
                            is emitted alone (see stderr warning). PDF-only;
                            ignored in image-export mode. Ignored with
                            -o - (stdout).

                            **Scheme change (schema v2):** the chunk
                            filename scheme changed from
                            `{base}_{idx}{ext}` (0-indexed, unpadded) in v1
                            to `{base}_{NNN}{ext}` (1-indexed, zero-padded
                            to max(3, len(str(total))) digits) in v2. Sort
                            order is now lexically stable. Automation that
                            hardcoded `{base}_0.pdf` must update to
                            `{base}_001.pdf` (or glob `{base}_*.pdf` and
                            sort lexically).

Engine:
  --mode {fast,standard,safe}      Default: standard.
  --bg-codec {jpeg,jpeg2000}       Default: jpeg. jpeg2000 is ~10-20% smaller
                                   on paper textures but adds ~1-2 s/page
                                   (demoted to jpeg in fast mode).
  --target-bg-dpi INT              Default: 150.
  --target-color-quality INT       Default: 55 (0-100 scale).
  --bg-chroma {4:4:4,4:2:2,4:2:0}  Default: 4:4:4 (preserves colored text;
                                   4:2:0 is smaller but smears color on thin
                                   strokes).
  --force-monochrome               Collapse mixed/photo pages to the text-only
                                   route. Emits
                                   page-N-color-detected-in-monochrome-mode
                                   warnings when color content is flattened.
  --legal-mode                     RESERVED: raises NotImplementedError until
                                   a later phase (CCITT G4 not yet built).
  --target-pdfa                    Target PDF/A-2u output.

Image-export (requires --output-format {jpeg,png,webp} or a matching -o extension):
  --image-dpi INT            DPI for image-export formats. Default: 150.
                             300 for archival. Max: 1200 (above this =
                             memory-exhaustion risk; argparse rejects with
                             exit 2).
  --jpeg-quality INT         JPEG quality 0-100. Default: 75.
  --png-compress-level INT   PNG zlib compression level 0-9. 0=no compression,
                             9=max. Default: 6 (Pillow standard).
  --webp-quality INT         WebP quality 0-100. With --webp-lossless this
                             controls encoder effort rather than fidelity.
                             Default: 80.
  --webp-lossless            Encode WebP losslessly (bigger file, pixel-exact
                             decode). Default: lossy WebP at --webp-quality.

OCR:
  --ocr / --no-ocr           Default: --no-ocr. --ocr embeds a searchable
                             text layer (adds ~5 s/page).
  --ocr-language STR         Default: eng.

Safety:
  --allow-signed-invalidation    Explicit opt-in for signed PDFs.
  --allow-certified-invalidation Stricter opt-in for certifying signatures
                                 (/Perms/DocMDP).
  --preserve-signatures          When the input is signed, pass the bytes
                                 through verbatim (no compression) so the
                                 signature stays valid. Mutually exclusive
                                 with --allow-signed-invalidation.
                                 Emits warning [W-PASSTHROUGH-SIGNED].
  --allow-embedded-files         Keep /EmbeddedFiles instead of stripping.
  --password-file PATH           Read password from file. Never use
                                 --password on CLI; env var
                                 HANKPDF_PASSWORD also works.

Verifier (content-drift gate):
  --verify                       Enable the content-drift verifier (off by
                                 default since v0.0.x). Re-rasterizes the
                                 output, re-runs OCR, compares against
                                 input. Adds ~2-5 s/page. Use for clinical /
                                 legal / archival runs where post-hoc
                                 content-preservation proof matters. Drift
                                 behavior is controlled by --accept-drift
                                 (default: abort).
  --skip-verify                  Hidden alias for the default behavior
                                 (verifier skipped). Retained as a no-op for
                                 backward compatibility.
  --accept-drift                 Write the output PDF even if the
                                 content-preservation verifier flags drift.
                                 Keeps the full-quality (300 DPI source)
                                 pipeline, unlike --mode fast which also
                                 lowers DPI. Drift is recorded in
                                 report.warnings. Use only after visually
                                 verifying the output.

Per-page MRC gate:
  --per-page-min-image-fraction FLOAT  Default: 0.30. Per-page threshold on
                                 image_xobject_bytes / page_byte_budget.
                                 Pages below this fraction are emitted
                                 verbatim into the output PDF (no rasterize,
                                 no compose, no verify); pages at or above
                                 go through the full MRC pipeline. When NO
                                 page meets the threshold, the whole-doc
                                 passthrough shortcut returns the input
                                 bytes unchanged with status="passed_through"
                                 and warning passthrough-no-image-content.
                                 Set to 0.0 to disable the gate (force every
                                 page through the pipeline). The gate is
                                 also disabled by --re-ocr, --strip-text-layer,
                                 --legal-mode, and --verify (see §4.1b).

Selection:
  --pages SPEC               Restrict processing to a subset of pages.
                             1-indexed. Accepts comma-separated single
                             pages and ranges, e.g. '1,3-5,10' or '1-3' or
                             '5'. Output PDF contains only the selected
                             pages in their original order. Useful for
                             smoke tests. A single range is capped at
                             1,000,000 pages to prevent DoS via
                             set(range(1, 10**11)).

Limits:
  --max-pages INT
  --max-input-mb FLOAT       Default: 2000.
  --max-workers INT          Per-page parallelism. 0 (default) = auto
                             (cpu_count-2, min 1). 1 = serial. N>1 =
                             exactly N workers. Each worker gets its own
                             single-page PDF slice, never the whole
                             source.
  --max-worker-memory-mb INT Per-page worker memory cap in MB. Default
                             (when not set) is computed as
                             min(max(8 GB, 16 × input_size), 16 GB) and
                             then clamped by an aggregate-envelope check
                             against psutil.virtual_memory().available
                             × 0.7 / n_workers. If the requested cap ×
                             n_workers would exceed 70% of host RAM at
                             startup, compress() raises
                             HostResourceError → exit 19
                             ([E-HOST-RESOURCE]). Workers self-apply the
                             cap on init via RLIMIT_AS (Linux/macOS) or a
                             Job Object (Windows). A parent-side psutil
                             RSS watchdog runs as a redundant backstop
                             with cooperative shutdown — workers check a
                             shared mp.Event at safe-write boundaries
                             (NOT psutil.Process.terminate() mid-write).
                             Pass 0 to disable the cap entirely (escape
                             hatch for tests / hosts without setrlimit
                             support).

Reporting:
  --report {text,json,jsonl,none}  Default: text.
  --quiet                          Suppress non-error output (also
                                   suppresses tqdm progress bars and
                                   per-chunk write lines).

Meta:
  -h, --help
  -V, --version
  --doctor                   Print environment sanity report and exit.
```

### 2.2 Exit codes

Stable across versions. Scripts MUST branch on these, not on parsed stdout.

| Code | Meaning | Script action |
|---|---|---|
| 0 | Compressed, verifier passed (or verifier skipped) | Upload output |
| 2 | No-op: input already small enough (below `--min-input-mb` or ratio below `--min-ratio`) — OR argparse-level validation failure (see note below) | Upload input unchanged (success case); fix invocation (argparse case) |
| 10 | Refused: encrypted, no password | Prompt user or pass-through |
| 11 | Refused: digitally signed, no invalidate flag | Pass-through original |
| 12 | Refused: oversize (`--max-pages` or `--max-input-mb`) | Pass-through, alert ops |
| 13 | Refused: malformed / unrecoverable | Pass-through, log |
| 14 | Refused: malicious (resource-cap exceeded in sandbox) | Quarantine, alert security |
| 15 | Refused: certifying signature (/Perms/DocMDP), requires stricter opt-in | Pass-through; never silently override |
| 16 | Refused: decompression bomb (pixel-count cap exceeded) | Quarantine, alert security |
| 20 | Verifier failed — content drift detected | Do NOT upload output; keep original |
| 30 | Engine internal error | Retry; then fall back to original |
| 40 | Invalid CLI usage (caught after parse: missing INPUT/-o, unreadable password file, out-of-range `--pages`, empty `--pages` set, stdout chosen with multi-page image export) | Fix invocation |
| 2xx | Reserved for transient failures (network, license server, etc.); not used by the local CLI today | Retry with exponential backoff |

Additional codes (added in v0.3.0):

- `17` — `E-ENV-MISSING`: a native dependency is missing or below the
  supported floor. Run `hankpdf --doctor` for the full report.
- `18` — `E-MEM-CAP`: a per-page worker exceeded its memory cap.
- `19` — `E-HOST-RESOURCE`: insufficient host memory for the requested
  worker count × cap. Reduce `--max-workers` or free memory.

**Note on argparse-level exit 2.** Flag values that fail argparse's own type validation — most notably `--image-dpi` outside the `[1, 1200]` range — exit with code `2` via argparse's built-in error handling, NOT via our `EXIT_USAGE = 40`. This is an intentional departure from the numeric contract: wrapping argparse to convert its `ArgumentTypeError` into exit 40 would require us to intercept `parse_args()` error handling, which would also suppress argparse's auto-generated help/usage output on malformed flags. Script authors who branch on exit codes should treat `2` as either "passthrough success" or "argparse rejected a flag value" and disambiguate via stderr (argparse writes `hankpdf: error: …` on the failure path).

### 2.3 JSON report schema

`--report json` writes one object to stdout; `--report jsonl` writes one object per line (for batch mode).

```json
{
  "schema_version": 5,
  "input": "chart-2026-04-21.pdf",
  "output": "chart-2026-04-21.compressed.pdf",
  "status": "ok",
  "exit_code": 0,
  "input_bytes": 818365952,
  "output_bytes": 6291456,
  "ratio": 130.1,
  "pages": 212,
  "wall_time_ms": 94321,
  "engine": "mrc",
  "engine_version": "1.4.2",
  "verifier": {
    "status": "pass",
    "ocr_levenshtein": 0.004,
    "ssim_global": 0.975,
    "ssim_min_tile": 0.94,
    "digit_multiset_match": true,
    "structural_match": true,
    "failing_pages": [],
    "color_preserved": true
  },
  "warnings": ["page-47-skipped-small-print", "pages-skipped-verbatim-3"],
  "strips": ["/JavaScript", "/OpenAction"],
  "input_sha256": "3a7b...",
  "output_sha256": "9f2c...",
  "reason": null,
  "pages_skipped_verbatim": [12, 47, 88]
}
```

### 2.4 Streaming

```bash
cat input.pdf | hankpdf - -o - > output.pdf
```

- Input stdin requires `stdin` to be seekable OR the full input must fit in a tmpfs buffer (we buffer up to `--max-input-mb`).
- Output stdout is valid only if report format is `none` or `json` (JSON goes to stderr in that case).

### 2.5 Batch mode

```bash
hankpdf --in-dir ./in --out-dir ./out --jobs 4 --report jsonl
```

- Mirrors directory structure.
- Emits one JSONL line per input on stdout.
- Returns exit 0 if every file returned 0/2; otherwise exit 1 and JSONL entries record individual exit codes.

## 3. (Reserved — CLI is the only user-facing surface)

There is no GUI for HankPDF. The CLI (§2) and Python API (§1) are the only surfaces the user sees. Users who want drag-drop integration can add `hankpdf` to their OS's "Open with" list themselves — Windows Explorer "Send To", macOS Automator / Shortcuts, Linux `.desktop` files. Those are user-configured shell conveniences, not product features we ship.

## 4. Weird-PDF taxonomy: detection + policy

Complete enumeration of classes and the policy each receives.

| Class | Detection | Policy | Exit code (if CLI) |
|---|---|---|---|
| Encrypted (user password) | `pikepdf.PasswordError` without key | If `options.password`: decrypt; else refuse with `EncryptedPDFError` | 10 |
| Encrypted (owner password only) | Encrypted but opens without password | Proceed (owner-only doesn't gate access) | 0 |
| Encrypted (certificate/public-key) | `/Encrypt` `/Filter /Adobe.PPKLite` | Refuse: pass-through | 11 |
| Digitally signed | `/AcroForm /SigFlags` bit 1, or `/Sig` fields | Refuse unless `--allow-signed-invalidation`; audit-log invalidation | 11 |
| Corrupt xref | qpdf repair warnings on open | Auto-repair; log; re-hash content; proceed | 0 |
| Linearized | `/Linearized` dict at file start | Delinearize, recompress, re-linearize via qpdf `--linearize` | 0 |
| Layered (OCGs) | `/OCProperties` present | Preserve layers; compress per-layer images | 0 |
| Tagged PDF | `/StructTreeRoot` + `/MarkInfo /Marked true` | Preserve tags; pass-through unless user opts in via `mode=fast` | 0 |
| Already contains JBIG2 | Any stream filter `/JBIG2Decode` | Never re-decode outside sandbox; pass through stream opaquely | 0 |
| Form XObjects (`/Type /XObject /Subtype /Form`) | pikepdf object walk | Preserve as-is; don't rasterize | 0 |
| CMYK content | Image `/ColorSpace /DeviceCMYK` or ICC | Preserve CMYK in output (legal color fidelity) | 0 |
| Unusual rotation | `/Rotate` 90/180/270 | Preserve metadata; never pre-rotate pixels | 0 |
| Huge page (>200"×200") | `/MediaBox` dimensions | Rasterize at adaptive DPI; cap pixel count 100 MP/page; refuse >100 MP | 12 |
| PDF/A-1b/2b/2u input | XMP `pdfaid:part` + `pdfaid:conformance` | Preserve or strengthen level; refuse if we can't meet same level | 13 |
| PDF/A-3 with embedded files | `pdfaid:part=3` + `/EmbeddedFiles` | Refuse unless `--allow-embedded-files`; log each embedded file SHA-256 | 12 |
| Contains `/JavaScript`, `/JS` | Object walk | Strip; log to sidecar manifest | 0 |
| Contains `/OpenAction`, `/AA` | Object walk | Strip; log to sidecar manifest | 0 |
| Contains `/Launch` | Object walk | Strip; log to sidecar manifest | 0 |
| Contains `/EmbeddedFiles` | Name tree | Strip unless `--allow-embedded-files`; log to sidecar manifest | 0 |
| Contains `/RichMedia` or `/3D` | Annotation `/Subtype` | Strip; log to sidecar manifest | 0 |
| Contains `/GoToR` with external URI | Action object | Strip; log to sidecar manifest | 0 |
| Malicious (JBIG2 bomb, xref loop, recursive forms) | Sandbox resource cap tripped | SIGKILL the worker child; `MaliciousPDFError` | 14 |
| Already aggressively compressed (DCT quality <40) | Heuristic on existing image streams | Return input unchanged; status=`passed_through` | 2 |
| Oversize input (> `max_input_mb` or `max_pages`) | Size check before triage | Refuse; `OversizeError` | 12 |
| Decompression bomb (pixel-count cap exceeded) | Total pages × page dims × bit depth > configured cap, OR Pillow raises `DecompressionBombError` on decode | Refuse; `DecompressionBombError`; quarantine | 16 |
| Hybrid xref (`/XRefStm`) | Trailer has `/XRefStm` entry | Proceed; log warning — qpdf silently ignores `/Prev` chains inside xref streams (historical revisions lost) | 0 |
| Chained incremental updates | Multiple `%%EOF` + `startxref` sections | Proceed; count revisions; log "collapsed N revisions" to sidecar manifest (audit trail impact) | 0 |
| `/Metadata` points to non-stream object | `type(pdf.Root.Metadata) != Stream` (pikepdf #349, #568 class) | Swallow with try/except; treat as "no XMP available"; never crash verifier | 0 |
| PDF 2.0 input | `%PDF-2.0` header + `/Version /2.0` in catalog | Proceed; log; downgrade to 1.7 for output; warn that PDF 2.0-only features (associated files, namespaces, enhanced tags) are discarded | 0 |
| `/Perms` (permission signature) present | `trailer.Encrypt.Perms` present | Proceed with owner decrypt; log "removed permission signature" to sidecar manifest | 0 |
| AcroForm with `/CO` calculation order + JavaScript | Walk `/AcroForm/CO`; scan `/AA`/`/A`/`/JS` | If stripping JS: flatten calculated fields to current displayed value (do not leave form blank); log | 0 |
| Non-rectilinear `/Rotate` (not 0/90/180/270) | `/Rotate` not in {0,90,180,270} | Treat as malformed; clamp to nearest valid or reject | 13 |
| `/Length` disagrees with actual stream bytes on page `/Contents` | Read-and-compare during triage | qpdf repairs silently; log every occurrence to sidecar manifest for audit | 0 |
| Known-bad producer fingerprint | Scan `/Info /Producer` against list (Canon iR MF64x, HP M29w, Brother ADS, Kyocera TASKalfa, Ricoh Aficio 2232, eCopy ShareScan) | Route to high-caution mode: sandbox stricter, force deterministic ID, run extra qpdf --check validation | 0 |
| `qpdf --check` warnings while pikepdf opens clean | Run qpdf --check side-channel; compare warning count | Proceed; log full warning set to sidecar manifest; add to verifier scrutiny | 0 |
| Certifying signature (DocMDP) | `/AcroForm/SigFlags` bit 1 + `/Perms/DocMDP` present | Refuse by default; require separate `--allow-certified-invalidation` flag (stricter than regular signature opt-in) | 15 |
| Pages with missing `/Contents` (empty page) | Page object walk | Proceed; don't error (common in driver-buggy MFP output) | 0 |
| Already-optimized input (own-output or similar MRC) | Image XObjects contain existing `/JBIG2Decode` + `/SMask` pattern, OR DCT quality heuristic <40 | Pass-through (status=`passed_through`, exit 2); re-processing inflates size | 2 |
| Huge `/ObjStm` (> 10,000 objects in single ObjStm) | ObjStm size walk | Refuse as resource-bomb candidate | 14 |
| Filename contains non-NFC Unicode | Unicode normalization check at ingest | Normalize to NFC; log `filename_renormalized` counter (macOS NFD → Linux NFC silent data-loss class) | 0 |

### 4.1b Per-page MRC gate

Before the parallel page split, every page is scored on a cheap stream-length signal: `image_xobject_bytes / page_byte_budget`, where `page_byte_budget = len(content_stream) + sum(/XObject /Image /Length) + sum(/XObject /Form /Length)`. Pages at or above `--per-page-min-image-fraction` (default `0.30`) go through the full MRC pipeline; pages below are emitted verbatim into the output PDF (no rasterize, no classify, no compose, no verify).

When **no** page meets the threshold, `compress()` returns the input bytes unchanged via the whole-doc passthrough shortcut: `status="passed_through"`, `exit_code=2`, warning `passthrough-no-image-content`, and `pages_skipped_verbatim=()` (empty — there's nothing to skip when the whole doc is the passthrough).

On partial runs, `pages_skipped_verbatim` carries the 0-indexed page indices of skipped pages and the aggregate warning `pages-skipped-verbatim-N` is emitted, where N is the count.

**Disable conditions.** The gate is bypassed (every page goes through the full pipeline) when any of:

- `--re-ocr` (force Tesseract on every page)
- `--strip-text-layer` (explicit text removal request)
- `--legal-mode` (CCITT G4 / archival profile — every page must be re-encoded)
- `--verify` (verifier needs full-pipeline output to compare against; otherwise synthetic per-page verdicts would pollute the aggregate metrics)
- `--per-page-min-image-fraction 0.0` (threshold meets every page)

**Conservative bias.** The classifier walks each page's *direct* `/Resources/XObject` dict only. Image bytes inside Form XObject sub-resources are NOT counted (no recursive walk), and resources inherited from the parent `/Pages` tree are NOT consulted. Both biases push toward conservatism (more pages routed to MRC), which is the safe direction — the gate is a pre-filter, not a verifier.

The gate is the union of two early-exit signals that previously lived only at the whole-doc level: the legacy "no image content" passthrough (now collapses naturally to "every page below threshold"), and the per-page MRC selection (skip individual text pages within a mixed doc). See `hankpdf/engine/page_classifier.py` and `docs/superpowers/specs/2026-04-27-per-page-selective-mrc-design.md`.

### 4.4 Strip log format

Each strip is recorded in `report.strips` as a stable enum name:

```
/JavaScript
/OpenAction
/AdditionalActions
/Launch
/EmbeddedFiles
/RichMedia
/3DAnnotation
/GoToR-External
/PrinterMark
```

## 5. Canonical input hash (exposed for caller use)

HankPDF computes a canonical-content hash of every input and includes it in the `CompressReport` / sidecar manifest. Callers who run HankPDF in a pipeline can use this for dedup, caching, or audit. HankPDF itself stores nothing and does no caching.

### 5.1 Canonicalization

1. Open input with pikepdf.
2. Remove `/ID` from trailer.
3. Remove `/Info /CreationDate`, `/Info /ModDate`.
4. Strip XMP fields: `xmp:CreateDate`, `xmp:ModifyDate`, `xmp:MetadataDate`, producer strings.
5. Re-serialize with pikepdf's deterministic object output mode (stable object numbering, lexicographic key ordering in dictionaries, no compression of cross-reference stream metadata).
6. Hash the re-serialized bytes with SHA-256.

### 5.2 What this catches

- Same-content documents that only differ in metadata timestamps, producer strings, or random `/ID` arrays → same hash. (Useful: two email sends of the same scan dedupe.)
- Two concatenations of the same pages with different page-tree structure → different hashes. Treated as distinct.

### 5.3 Where it appears

`CompressReport.canonical_input_sha256` and sidecar manifest `canonical_input_sha256` field. Caller decides what to do with it.

## 6. Sidecar manifest schema

Written alongside every successful output as `<output-basename>.hankpdf.json`.

```json
{
  "schema_version": 5,
  "input_sha256": "...",
  "canonical_input_sha256": "...",
  "output_sha256": "...",
  "engine": "mrc",
  "engine_version": "1.4.2",
  "compressed_at": "2026-04-21T17:35:00Z",
  "compression": {
    "input_bytes": 818365952,
    "output_bytes": 6291456,
    "ratio": 130.1,
    "pages": 212
  },
  "strips": ["/JavaScript", "/OpenAction"],
  "verifier": { /* VerifierResult */ },
  "warnings": ["page-47-skipped-small-print"],
  "source": {
    "cli_version": "1.4.2",
    "host_platform": "linux-x86_64",
    "options": { /* redacted CompressOptions — no password */ }
  }
}
```

## 7. Error model

### 7.1 Exception hierarchy

```
CompressError
├── EncryptedPDFError
├── SignedPDFError
├── MaliciousPDFError
├── ContentDriftError
├── OversizeError
├── CorruptPDFError
├── HostResourceError
└── LicenseError
```

### 7.2 Structured warning codes

Emitted via `report.warnings` as stable string enums (never free-text).

```
page-N-skipped-small-print
page-N-ocr-low-confidence
page-N-color-detected-in-monochrome-mode
page-N-already-optimized
xref-repaired
strip-javascript
strip-openaction
strip-embedded-files
signature-passthrough
encrypted-owner-only
pdfa-downgrade-refused
```

### 7.3 Human-readable messages

Displayed via CLI default report mode. Never includes PHI or filenames verbatim — always uses generic phrasing.

| Error | Message |
|---|---|
| `EncryptedPDFError` | "This PDF is password-protected. Provide the password or keep the original." |
| `SignedPDFError` | "This PDF has a digital signature. Shrinking would invalidate it. Keep original or confirm invalidation." |
| `CertifiedSignatureError` | "This PDF carries a certifying signature (author-certified, DocMDP). Invalidating it carries stronger legal risk than an ordinary signature. Contact support before proceeding." |
| `MaliciousPDFError` | "This PDF could not be processed safely. Please contact support with this reference code." |
| `DecompressionBombError` | "This PDF's images are unusually large and could exhaust memory. Refusing to process for safety. Contact support with this reference code." |
| `ContentDriftError` | "We couldn't compress this PDF without losing detail. Please keep the original." |
| `OversizeError` | "This PDF is larger than we can process ({size} MB). Please split it first or contact support." |
| `CorruptPDFError` | "This PDF appears damaged. Please re-scan or re-export it." |
| `EnvironmentError` | "HankPDF environment check failed. Run `hankpdf --doctor` to see what's missing or out of date (e.g. qpdf must be ≥11.6.3 to avoid a known data-loss bug)." |

## 8. Local logging and diagnostics

HankPDF is a local tool. There is no metrics endpoint, no telemetry, no external log target. All observability is local-only and exists so the user (or a script wrapping HankPDF) can diagnose what happened on their own machine.

### 8.1 CLI report

Every invocation emits a `CompressReport` (see §1.1). For CLI use, `--report {text|json|jsonl|none}` controls output format. Schema frozen in §2.3.

### 8.2 Logs (local only)

- Written to **stderr** by default; `--log-file PATH` redirects to a file; `--quiet` suppresses non-error lines.
- Structured JSON when `--log-format json`; human-friendly single lines otherwise.
- `INFO` default; `DEBUG` includes internal state (segmentation tile counts, codec choices).
- **Content hygiene** (even though logs stay local, users who wrap HankPDF in their own pipelines will pipe logs elsewhere):
  - Raw filenames, OCR text, PDF content, passwords, `/Title`, `/Author`, `/Subject`, `/Keywords`, `/Producer` are never logged verbatim.
  - Filenames appear as `sha1(basename)[:8]…basename[-8:]` in log lines. Implemented via `hankpdf.utils.log.redact_filename()`.
  - CI lint rule bans `logger.info(f"...{filename}...")` and any log call with f-strings containing `path`, `filename`, `basename`, `producer`, `ocr_text`, `content`. Route everything through the `redact_*` helpers.

### 8.3 Diagnostics (`--doctor`)

Prints environment sanity report and exits — never runs compression. Output includes:
- HankPDF version + engine version
- Python version + free-thread-build flag (should be "not built" for v1)
- Platform, CPU count, physical memory
- Tesseract version + tessdata pack SHA-256
- qpdf version (must be ≥11.6.3)
- pdfium binary revision from the bundled pypdfium2
- OpenJPEG version (must be ≥2.5.4)
- jbig2enc vendor commit hash
- PIL `MAX_IMAGE_PIXELS` setting
- Exit 0 if all floor checks pass; exit 41 if any fail (with the specific fix hint).

### 8.4 Self-counters (local, opt-in)

By default, nothing persists between runs. Users who want to see cumulative stats across many runs can pass `--stats-file PATH`; HankPDF appends a JSONL entry per job to that file. This is **the user's file**, written only when they opt in. No rotation, no upload, no analytics.

### 8.5 Structured warning codes (emitted in CompressReport.warnings)

Phase 2b adds the following per-page and per-job counters / warnings:

- `strategy_distribution{class="text_only"|"photo_only"|"mixed"|"already_optimized"}` — emitted by `compress()` once per page (see `CompressReport.strategy_distribution`). The `"already_optimized"` count comes from the per-page MRC gate (introduced in v4) and may be non-zero on partial-passthrough runs where some pages were copied verbatim from the input.
- `passthrough-no-image-content` — emitted when every page in the input has image-byte-fraction below `min_image_byte_fraction` (default 0.30); whole-doc passthrough fires at the top of `compress()` and the input is returned unchanged. Co-emitted with `status="passed_through"`.
- `pages-skipped-verbatim-N` — emitted when SOME but not all pages were copied verbatim (partial run). N is the count; the 0-indexed page numbers are in `report.pages_skipped_verbatim`. Status remains `"ok"` because the MRC pages produced real output.
- `page-N-jbig2-fallback-to-flate` — emitted when jbig2enc errored on page N and compose fell back to flate. Distinct from `jbig2enc-unavailable-using-flate-fallback` (job-wide, emitted once up front when the jbig2 binary is missing).
- `page-N-color-detected-in-monochrome-mode` — emitted when `force_monochrome=True` flattened a page that contained color content. `force-monochrome-discarded-color-on-N-pages` is the job-wide aggregate.
- `page-N-text-only-demoted-to-mixed-color-detected` — emitted when classify_page routed a page to TEXT_ONLY but the channel-parity check detected color, forcing fallback to the MRC route.
- `page-N-anomalous-ratio-{N}x-safe-verify` — emitted when per-page ratio exceeds 200× on a non-TEXT_ONLY page; the page's tile-SSIM floor is tightened to the `safe` threshold.
- `bg-codec-jpeg2000-demoted-fast-mode` — emitted once per job when the user requested `bg_codec=jpeg2000` but `mode=fast` demoted it to JPEG for latency reasons.
- `verifier-skipped` — emitted once per job when `options.skip_verify` is True (the default; also the `--skip-verify` / absence-of-`--verify` CLI path).
- `verifier-fail-{accept-drift|fast-mode}-pages-[…]` — emitted when the verifier flagged drift but the job was configured to warn instead of abort (`options.accept_drift=True` or `options.mode="fast"`). Lists 1-indexed failing pages.
- `forkserver-preload-failed-<ExceptionType>` — emitted when `set_forkserver_preload(["hankpdf.engine", …])` raises `ValueError` or `RuntimeError` (typically because a preloaded module instantiates multiprocessing objects at import time). Workers still function, but each re-imports the heavy module chain (numpy/cv2/pikepdf ~ 2-3 s each per worker). Exception-type suffix lets ops grep for the specific cause.

#### 8.5.1 Stderr-only CLI warnings (not part of CompressReport.warnings)

The CLI prints the following to **stderr** when `--quiet` is not set. These are human-readable diagnostics only; they are not appended to `CompressReport.warnings` (the CLI wraps `compress()` so these come from the CLI layer, not the library).

Every stderr warning line starts with `[hankpdf] warning [CODE]:` and every stderr error line starts with `[hankpdf] error [CODE]:` so batch scripts can grep by code without depending on exact English wording:

```
grep -F "[W-CHUNKS-EXCEED-CAP]" job.log   # portable + stable across releases
```

Stable codes (see `hankpdf/cli/warning_codes.py` — the `CliWarningCode` Literal is the source of truth):

- `W-MAX-OUTPUT-MB-IMAGE-MODE` — `--max-output-mb` is combined with an image-export format. The flag is PDF-only; image-export mode ignores it.
- `W-MAX-OUTPUT-MB-STDOUT` — `--max-output-mb` is combined with `-o -` (stdout) AND the merged output exceeds the cap. Stdout can't be split; the merged bytes are emitted.
- `W-OUTPUT-FORMAT-EXTENSION-OVERRIDE` — `--output-format` contradicts the `-o` file extension. The output-format flag wins.
- `W-CHUNKS-EXCEED-CAP` — one or more emitted `{base}_NNN{ext}` chunks exceed `--max-output-mb` because an individual page is larger than the cap. Cannot be split further; emitted alone.
- `W-STALE-CHUNK-FILES` — pre-existing `{base}_NNN{ext}` files with an index greater than the new chunk count remain in the output directory. The new run does NOT overwrite them (different indices). User must remove manually. **This warning is NOT suppressed by `--quiet`** — it affects correctness of downstream automation (cron jobs that glob `{base}_*.pdf`).
- `W-SINGLE-CHUNK-OVERSIZE` — single-chunk output exceeds the `--max-output-mb` cap because the PDF contains a single oversize page. Cannot be split further; the oversize output was retained.
- `W-VERIFIER-SKIPPED` — the content-preservation verifier was SKIPPED (default). Output was NOT content-checked against input. Use `--verify` to enable.
- `W-VERIFIER-FAILED` — the verifier flagged drift but the job was configured to warn instead of abort (via `--accept-drift` or `--mode fast`).
- `W-IMAGE-EXPORT-PARTIAL-FAILURE` — image-export mode failed mid-way through a multi-page export. Emitted alongside the list of pages written before the failure. Exit code depends on the underlying cause (`EXIT_MALICIOUS=14`, `EXIT_DECOMPRESSION_BOMB=16`, `EXIT_ENGINE_ERROR=30`).
- `W-CHUNK-WRITE-PARTIAL-FAILURE` — chunk write failed mid-way (disk full / permission / path error). Exit code `EXIT_ENGINE_ERROR=30`.
- `[W-PASSTHROUGH-SIGNED]` — signed input passed through verbatim
  (`--preserve-signatures`).
- `[W-MEM-RSS-WATCHDOG]` — RSS watchdog observed a worker exceeding the
  cap; sets the shared cooperative-abort event so all in-flight workers
  drain at the next safe-write boundary.
- `[W-CAPS-UNAVAILABLE]` — platform has no memory-cap primitive (rare;
  e.g., misconfigured Docker without `setrlimit` capability).
- `[W-CAPS-FAILED]` — kernel rejected the cap call. Worker continues
  without the cap; psutil watchdog still applies.
- `[W-WATCHDOG-DIED]` — RSS watchdog thread died unexpectedly; the
  per-worker memory cap was effectively unenforced for at least part
  of the run. Re-run with reduced `--max-workers` if this fires.

Refusal / failure error codes (`E-*` — tag `[hankpdf] error` lines):

- `E-INPUT-ENCRYPTED` — input is encrypted and no password was supplied. Exit code `EXIT_ENCRYPTED=10`.
- `E-INPUT-SIGNED` — input carries a digital signature; `--allow-signed-invalidation` not supplied. Exit code `EXIT_SIGNED=11`.
- `E-INPUT-CERTIFIED` — input carries a certifying signature (`/Perms /DocMDP`); `--allow-certified-invalidation` not supplied. Exit code `EXIT_CERTIFIED_SIG=15`.
- `E-INPUT-OVERSIZE` — input exceeds `--max-input-mb` or `--max-pages`. Exit code `EXIT_OVERSIZE=12`.
- `E-INPUT-CORRUPT` — pikepdf / qpdf could not parse the input. Exit code `EXIT_CORRUPT=13`.
- `E-INPUT-MALICIOUS` — input tripped a sandbox resource cap (JBIG2 bomb, xref loop). Exit code `EXIT_MALICIOUS=14`.
- `E-INPUT-DECOMPRESSION-BOMB` — declared or rendered pixel count exceeds the decompression-bomb cap (~715 Mpx). Exit code `EXIT_DECOMPRESSION_BOMB=16`.
- `E-INPUT-NOT-PDF` — input did not start with `%PDF-` magic bytes. Exit code `EXIT_CORRUPT=13` (reserved; emitted by future strict-magic-check path).
- `E-VERIFIER-FAIL` — content-drift verifier aborted the job. Exit code `EXIT_VERIFIER_FAIL=20`.
- `E-ENGINE-ERROR` — generic `CompressError` not covered by the more specific codes above. Exit code `EXIT_ENGINE_ERROR=30`.
- `E-TIMEOUT-PER-PAGE` — a page exceeded `per_page_timeout_seconds`. Exit code `EXIT_ENGINE_ERROR=30`.
- `E-TIMEOUT-TOTAL` — the run exceeded `total_timeout_seconds`. Exit code `EXIT_ENGINE_ERROR=30`.
- `E-OCR-TIMEOUT` — a Tesseract subprocess exceeded the per-page timeout (raised from inside `tesseract_word_boxes`). Exit code `EXIT_ENGINE_ERROR=30`.

Exit codes disambiguate the refusal class for scripts that key on `$?`; the `E-*` codes are for scripts that tee stderr to a log and grep by code. Both interfaces are stable.

## 10. Build matrix

| Target | Python | Artifact | Notes |
|---|---|---|---|
| PyPI wheel + sdist | 3.14 | Universal pure-Python wheel (`py3-none-any`) if feasible; per-platform wheels only if a pure-Python wheel can't express the native-dep declarations cleanly | Published via GitHub OIDC trusted publishing — no API tokens |
| Docker image | 3.14 | Multi-arch (amd64 + arm64) | Debian slim or wolfi base; all native deps (pdfium, Tesseract, jbig2enc, OpenJPEG, qpdf) baked in; non-root `hankpdf` user; read-only rootfs friendly; image size target < 300 MB |

**Not built**: standalone platform binaries (no PyInstaller artifacts), signed installers, code-signed releases. Users who need a binary can PyInstaller-build from source in a few minutes; we don't ship that as a product artifact.

## 11. Versioning and compatibility

- Semantic versioning: `MAJOR.MINOR.PATCH`.
- **Engine version** advances independently from CLI version when the compression algorithm changes in a way that produces byte-different outputs from the same input. Written to `CompressReport.engine_version` and sidecar manifest.
- **CLI exit codes and JSON report schema**: considered stable interfaces. Breaking changes require major version bump and a one-release deprecation warning.
- **Python API**: `compress()` signature stable within major version; internal modules may change freely.
- **Sidecar manifest schema**: versioned via `schema_version` field. Readers accept current and previous version.

### 11.1 CompressReport schema migration notes

- **v2 → v3** (DCR Wave 5, 2026-04-23):
  - `CompressReport.build_info` added (`BuildInfo | None`). Non-null when the process can resolve either an installed dist's `PKG-INFO` or the `/etc/hankpdf/build-info.json` shipped inside the Docker image. Carries `version`, `git_sha`, `build_date`, `base_image_digest`, `jbig2enc_commit`, `qpdf_version`, `tesseract_version`, `leptonica_version`, `python_version`, `os_platform`. Readers can tie a report back to the exact binary + native-dep versions that produced it; on-call uses it to diagnose "does this output predate the qpdf #1050 fix?"
  - `CompressReport.correlation_id` added (str, UUID4 hex). Auto-generated per-report via `default_factory`. CLI stamps every stderr line with a short form (`corr=<first-8-chars>`) so an on-call can grep a batch log slice and join it to a specific report. Library callers can pass their own id via `compress(..., correlation_id=...)`.
  - `schema_version` bumped from 2 to 3.
- v3 → v4 (additive): `pages_skipped_verbatim: tuple[int, ...]` field on `CompressReport`. `"already_optimized"` key in `strategy_distribution` may now be non-zero (was pre-allocated as 0 since v2). New warning codes `passthrough-no-image-content` and `pages-skipped-verbatim-N`. Per-page MRC gate documented above (§8.5).
  - `schema_version` bumped from 3 to 4.
  - No breaking field removals or renames — additive only.
- v0.3.0:
  - `schema_version` bumped from 4 to 5.
  - Added `signature_state ∈ {none, passthrough-preserved,
    invalidated-allowed, certified-invalidated-allowed}`.
  - Added `signature_invalidated: bool`.
  - Added `worker_memory_cap_bytes: int` and
    `worker_peak_rss_max_bytes: int`.
  - All additions are additive; v4 consumers reading by key continue
    to work.

- **v1 → v2** (DCR Wave 3, 2026-04-23):
  - `VerifierResult.status` gained the `"skipped"` literal (was only `"pass"`/`"fail"`). Readers that key on `verifier.status` must add a `"skipped"` branch; treat it as "no verification performed, caller should not assume pass."
  - `CompressReport.warnings` tuple now uses kebab-case codes (e.g., `verifier-skipped`, `bg-codec-jpeg2000-demoted-fast-mode`) for every job-wide warning. Page-local warnings retain the `page-{N}-…` convention.
  - `CompressReport.strategy_distribution` is now populated (was `{}` in v1). Keys: `text_only`, `photo_only`, `mixed`, `already_optimized`.
  - No breaking field removals or renames — additive only.

## 12. Testing interface

Test determinism guarantees:

- `triage()` is pure on bytes input.
- `segment_page()` is deterministic on a **single host with a single Tesseract + OpenCV + pdfium build**. It is NOT deterministic across hosts — Tesseract's LSTM uses float32 ops whose accumulation order varies by BLAS vendor, hardware, and platform. Tests that assert exact text equality run on a pinned CI image only.
- **Verifier** avoids cross-host non-determinism by caching input-OCR results keyed on SHA-256 of the rasterized input page image, and running both input-pass and output-pass OCR on the same host within the same process.
- Integration tests run against a corpus of public-domain PDFs (see [ROADMAP.md Phase 0](ROADMAP.md)).
- Golden-output tests record expected ratio **bands** (not exact sizes) — e.g., "this input must compress to between 4 and 8 MB." Pixel-level equality tests across hosts use SSIM tolerance, not byte equality.
- Cross-platform verifier drift is measured: same input on macOS + Linux CI; difference must stay under a documented tolerance budget (set empirically during Phase 1 spike).

Full test strategy in [ROADMAP.md Phase 0, Task T0.7](ROADMAP.md).
