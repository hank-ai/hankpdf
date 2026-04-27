"""Public data types for the HankPDF API.

See ``docs/SPEC.md`` §1.1 for the full specification. These dataclasses are
the source of truth; any change here is a public-API change.
"""

from __future__ import annotations

import uuid
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

    # OCR behavior:
    # - The output ALWAYS preserves an upstream text layer when one exists
    #   (and it passes a quality heuristic) — searchability is not opt-in
    #   for inputs that arrived searchable.
    # - ``ocr=True`` means "ensure searchable": if no upstream text layer is
    #   present, or the existing one looks like garbage, run Tesseract.
    # - ``strip_text_layer=True`` opts out: the output gets no text layer
    #   even when the input had one. Use for size-only workflows where
    #   searchability is unwanted.
    # - ``re_ocr=True`` opts out the other way: ignore the upstream text
    #   layer (even if good) and re-run Tesseract. Use when the upstream
    #   OCR is known to be wrong and you want a fresh Tesseract pass.
    ocr: bool = False
    ocr_language: str = "eng"
    strip_text_layer: bool = False
    re_ocr: bool = False

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
    max_pages: int | None = 10000  # None disables the gate (programmatic-only escape hatch)
    max_input_mb: float = 250.0
    # Per-page MRC gate: pages whose image_xobject_bytes / page_byte_budget
    # is below this threshold are copied verbatim from the input — no
    # rasterize, no MRC pipeline, no Tesseract. Default 0.30 catches
    # native-export PDFs (PowerPoint/Word output) where the MRC pipeline
    # can't beat already-efficient encoding. Set to 0.0 to disable the
    # gate (force every page through MRC). The flags strip_text_layer,
    # re_ocr, AND skip_verify=False (--verify) all also disable the gate
    # — --verify is included so verbatim pages don't feed synthetic
    # verdicts into _VerifierAggregator and pollute the aggregate
    # ssim/lev/digit metrics on partial-passthrough runs.
    min_image_byte_fraction: float = 0.30
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
class BuildInfo:
    """Identity of the HankPDF binary that produced a report (Wave 5 / C2).

    Populated from /etc/hankpdf/build-info.json (Docker image, B3) plus
    at-runtime probes of native deps (qpdf --version, tesseract --version,
    …). All fields are strings so the dataclass serializes cleanly through
    ``json.dumps(asdict(report))``.

    ``"?"`` is the sentinel for "not recorded" — chosen over None so the
    JSON shape is stable for downstream tooling (no ``if field is not None``
    per-field dance).
    """

    version: str
    git_sha: str
    build_date: str
    jbig2enc_commit: str
    qpdf_version: str
    tesseract_version: str
    leptonica_version: str
    python_version: str
    os_platform: str
    base_image_digest: str = "?"


def _new_correlation_id() -> str:
    """UUID4 hex; short enough for log lines, unique enough for grepping."""
    return uuid.uuid4().hex


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
    # 0-indexed page numbers that the per-page gate copied verbatim
    # from the input rather than running through the MRC pipeline.
    # Empty tuple = every page was MRC'd OR a whole-doc passthrough
    # fired (in which case status="passed_through").
    pages_skipped_verbatim: tuple[int, ...] = ()
    warnings: tuple[str, ...] = ()
    strips: tuple[str, ...] = ()
    reason: str | None = None
    # Schema v3 since Wave 5 (2026-04-23). v3 adds:
    #   - CompressReport.build_info (BuildInfo | None) — identity of the
    #     binary (git sha, build date, dep versions). None only on dev
    #     checkouts that can't resolve importlib.metadata.version().
    #   - CompressReport.correlation_id — a UUID4 hex string that also
    #     appears in every stderr line emitted during this run, letting
    #     on-call grep a batch log and tie each line back to its report.
    # v2 (Wave 3) added the "skipped" verifier status, kebab-case warning
    # codes, and populated strategy_distribution.
    schema_version: int = field(default=4)
    strategy_distribution: Mapping[str, int] = field(default_factory=dict)
    build_info: BuildInfo | None = None
    correlation_id: str = field(default_factory=_new_correlation_id)
