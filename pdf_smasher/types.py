"""Public data types for the HankPDF API.

See ``docs/SPEC.md`` §1.1 for the full specification. These dataclasses are
the source of truth; any change here is a public-API change.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

Mode = Literal["fast", "standard", "safe"]
Engine = Literal["mrc", "downsample-only"]
BgCodec = Literal["jpeg", "jpeg2000"]
ChromaSubsampling = Literal["4:4:4", "4:2:2", "4:2:0"]
Status = Literal["ok", "passed_through", "refused", "drift_aborted"]
VerifierStatus = Literal["pass", "fail", "skipped"]


@dataclass(frozen=True)
class CompressOptions:
    """Compression options. All fields have sensible defaults."""

    # Engine selection
    engine: Engine = "mrc"
    bg_codec: BgCodec = "jpeg"  # jpeg2000 is ~20% smaller on paper textures

    # Quality / ratio knobs
    target_bg_dpi: int = 150
    target_color_quality: int = 55  # 0-100 scale (not PSNR dB)
    bg_chroma_subsampling: ChromaSubsampling = "4:4:4"
    force_monochrome: bool = False
    mode: Mode = "standard"

    # Archival / legal profile.
    # str because SPEC §1.1 names the profile "ccitt-g4" (a string value);
    # None means "not requested". Bool was wrong — `False is not None` is True,
    # which made the guard in compress() always fire and raise NotImplementedError.
    legal_codec_profile: str | None = None
    target_pdf_a: bool = False

    # OCR behavior. Off by default — OCR roughly doubles wall time (verifier
    # runs Tesseract on input + output rasters regardless, but writing an
    # embedded text layer to the output is opt-in). Pass --ocr / ocr=True to
    # produce a searchable output.
    ocr: bool = False
    ocr_language: str = "eng"

    # Safety / behavior gates
    allow_signed_invalidation: bool = False
    allow_certified_invalidation: bool = False
    allow_embedded_files: bool = False
    accept_drift: bool = False  # if True, drift → warning instead of abort
    # Skip the content-drift verifier by default. It's slow (adds ~3s/page for
    # re-OCR on the output) and in its current form produces too many false
    # positives on realistic scans (Tesseract OCR is noisy between antialiased
    # source text and crisp binary-encoded output). Users who need the gate
    # (clinical, legal archival) can turn it on with verify=True / --verify.
    skip_verify: bool = True
    password: str | None = None

    # Thresholds
    min_input_mb: float = 0.0
    min_ratio: float = 1.5

    # Limits
    max_pages: int | None = None
    max_input_mb: float = 2000.0
    per_page_timeout_seconds: int = 120
    total_timeout_seconds: int = 1200
    photo_target_dpi: int = 200  # DPI for PHOTO_ONLY pages

    # Concurrency. Each worker gets its own single-page PDF slice, so
    # memory scales with (workers x one page's raster), not (workers x full source).
    #   0 = auto (cpu_count - 2, clamped ≥ 1)
    #   1 = serial (no process pool)
    #   N > 1 = exactly N workers
    max_workers: int = 0

    # Output
    emit_sidecar_manifest: bool = True
    output_pdf_version: str = "1.7"


@dataclass(frozen=True)
class VerifierResult:
    """Outcome of the content-preservation gate. See docs/SPEC.md §5."""

    status: VerifierStatus
    ocr_levenshtein: float
    ssim_global: float
    ssim_min_tile: float
    digit_multiset_match: bool
    structural_match: bool
    failing_pages: tuple[int, ...] = ()
    color_preserved: bool = True


@dataclass(frozen=True)
class TriageReport:
    """Triage-phase classification of an input PDF. See docs/SPEC.md §4."""

    pages: int
    input_bytes: int
    is_encrypted: bool
    is_signed: bool
    is_certified_signature: bool
    is_linearized: bool
    is_tagged: bool
    is_pdf_a: bool
    has_embedded_files: bool
    has_javascript: bool
    has_jbig2_streams: bool
    producer_fingerprint: str | None
    classification: Literal["proceed", "refuse", "pass-through", "require-password"]
    notes: tuple[str, ...] = ()


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
    """Structured progress event emitted during :func:`compress`.

    Carries no PHI — only phase, page indices, strategy names, and byte
    counts. CLI renders via tqdm; programmatic callers can log, collect
    metrics, or drive their own UI. ``total`` and ``current`` are both
    0 outside the per-page phase.
    """

    phase: ProgressPhase
    message: str
    current: int = 0  # 1-indexed page number during per-page phases
    total: int = 0  # total page count
    strategy: str | None = None  # for page_start / page_done
    ratio: float | None = None  # for page_done: true per-page file ratio
    input_bytes: int | None = None  # for page_done: this page's size in the input PDF
    output_bytes: int | None = None  # for page_done: this page's size in the output PDF
    # page_done only. Tri-state: True (pass), False (fail), None
    # (verifier did not run — skip_verify was set OR this phase doesn't
    # emit a verifier outcome). Callers keying on "pass unless False"
    # must also handle None as "no verification happened here".
    verifier_passed: bool | None = None


@dataclass(frozen=True)
class CompressReport:
    """Result of a compression run. See docs/SPEC.md §2.3 for schema."""

    status: Status
    exit_code: int
    input_bytes: int
    output_bytes: int
    ratio: float
    pages: int
    wall_time_ms: int
    engine: str
    engine_version: str
    verifier: VerifierResult
    input_sha256: str
    output_sha256: str
    canonical_input_sha256: str | None
    warnings: tuple[str, ...] = ()
    strips: tuple[str, ...] = ()
    reason: str | None = None
    schema_version: int = field(default=1)
    strategy_distribution: Mapping[str, int] = field(default_factory=dict)
