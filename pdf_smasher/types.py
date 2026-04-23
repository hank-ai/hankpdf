"""Public data types for the HankPDF API.

See ``docs/SPEC.md`` §1.1 for the full specification. These dataclasses are
the source of truth; any change here is a public-API change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping

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

    # Archival / legal profile
    legal_codec_profile: bool = False
    target_pdf_a: bool = False

    # OCR behavior
    ocr: bool = True
    ocr_language: str = "eng"

    # Safety / behavior gates
    allow_signed_invalidation: bool = False
    allow_certified_invalidation: bool = False
    allow_embedded_files: bool = False
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
