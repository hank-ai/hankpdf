"""HankPDF CLI entry point.

See docs/SPEC.md §2 for the full contract: flags, exit codes, report
schema.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict
from pathlib import Path

from hankpdf import (
    CertifiedSignatureError,
    CompressError,
    CompressOptions,
    CompressReport,
    ContentDriftError,
    CorruptPDFError,
    DecompressionBombError,
    EncryptedPDFError,
    MaliciousPDFError,
    OcrTimeoutError,
    OversizeError,
    PerPageTimeoutError,
    SignedPDFError,
    TotalTimeoutError,
    _enforce_input_policy,
    compress,
    triage,
)
from hankpdf.cli.warning_codes import emit as _warn
from hankpdf.cli.warning_codes import emit_error as _warn_error
from hankpdf.cli.warning_codes import emit_refusal as _refuse
from hankpdf.cli.warning_codes import line_prefix as _line_prefix
from hankpdf.engine.chunking import split_pdf_by_size
from hankpdf.engine.image_export import _MAX_IMAGE_DPI_LIB, iter_pages_as_images
from hankpdf.utils.atomic import _atomic_write_bytes
from hankpdf.utils.text import format_page_list_short


def _input_label(input_path: Path | None) -> str | Path | None:
    """Resolve the CLI input argument to a loggable label.

    Returns ``None`` for stdin or missing args — :func:`_line_prefix` and
    :func:`_warn` render that as the plain ``[hankpdf]`` prefix. Otherwise
    returns the path unchanged; redaction happens inside the warning_codes
    helpers per THREAT_MODEL.md §5.
    """
    if input_path is None:
        return None
    if str(input_path) == "-":
        return None
    return input_path


# Keep CLI cap in lockstep with the library cap (which is the real
# enforcer). The CLI layer just fails fast with a nicer argparse
# message instead of raising ValueError deep inside the generator.
_MAX_IMAGE_DPI = _MAX_IMAGE_DPI_LIB
_MAX_PAGES_RANGE = 1_000_000  # cap --pages "lo-hi" span to prevent DoS via
# set(range(1, 10**11)) materialization — see DCR Wave 1.


def _positive_float(raw: str) -> float:
    """argparse type for flags that must be > 0.

    Without this, `0` passes argparse, propagates through the full compress
    pipeline, and only crashes at the very end when a downstream validator
    rejects it. Failing fast at parse time keeps the error local to the
    CLI flag the user got wrong.
    """
    try:
        f = float(raw)
    except ValueError as e:
        msg = f"invalid float: {raw!r}"
        raise argparse.ArgumentTypeError(msg) from e
    if f <= 0:
        msg = f"must be > 0 (got {f})"
        raise argparse.ArgumentTypeError(msg)
    return f


def _positive_mb_value(raw: str) -> float:
    """argparse type for megabyte flags where int(value * 1024**2) must be
    >= 1 (i.e., the value must round up to at least one byte).

    ``_positive_float`` alone would accept ``1e-10`` — > 0 but rounds to
    zero bytes, which propagates as ``max_bytes=0`` into
    ``split_pdf_by_size`` and raises a bare ValueError deep in the pipeline.
    Reject at parse time so the user gets a clear message naming the flag.
    """
    f = _positive_float(raw)
    if int(f * 1024 * 1024) < 1:
        msg = (
            f"must round to >= 1 byte (got {f} MB = "
            f"{int(f * 1024 * 1024)} bytes). Try a value >= 0.000001."
        )
        raise argparse.ArgumentTypeError(msg)
    return f


def _positive_int(raw: str) -> int:
    """argparse type for int flags that must be >= 1.

    Used for --per-page-timeout-seconds and --total-timeout-seconds:
    zero or negative values make future.result(timeout=X) raise
    TimeoutError immediately on every page, producing a flood of
    PerPageTimeoutError from deep in the engine. Reject at parse
    time so the error names the right flag.
    """
    try:
        n = int(raw)
    except ValueError as e:
        msg = f"invalid int: {raw!r}"
        raise argparse.ArgumentTypeError(msg) from e
    if n < 1:
        msg = f"must be >= 1 (got {n})"
        raise argparse.ArgumentTypeError(msg)
    return n


# Upper bound on --max-workers. 256 matches the ProcessPoolExecutor default
# guidance (4 * cpu_count capped at 61 on Windows; anything beyond 256 is
# DoS-adjacent: each worker re-imports cv2/pikepdf/numpy for ~2-3s and holds
# a one-page raster worth of memory).
_MAX_WORKER_COUNT = 256


def _max_workers_value(raw: str) -> int:
    """argparse type for --max-workers. Reject negative or absurd values.

    0 = auto (cpu_count - 1, clamped ≥ 1) per CompressOptions.max_workers.
    1 = serial. 2..256 = explicit worker count.

    Reviewer B: negatives used to pass argparse and get silently coerced
    inside ``_resolve_worker_count``. Fail fast at parse time so the error
    names the right flag.
    """
    try:
        n = int(raw)
    except ValueError as e:
        msg = f"--max-workers: invalid int: {raw!r}"
        raise argparse.ArgumentTypeError(msg) from e
    if n < 0:
        msg = (
            f"--max-workers must be >= 0 (got {n}); 0=auto, 1=serial, "
            f"2..{_MAX_WORKER_COUNT}=explicit"
        )
        raise argparse.ArgumentTypeError(msg)
    if n > _MAX_WORKER_COUNT:
        msg = (
            f"--max-workers capped at {_MAX_WORKER_COUNT} (got {n}); each "
            "worker imports numpy/cv2/pikepdf and holds a one-page raster "
            "worth of memory"
        )
        raise argparse.ArgumentTypeError(msg)
    return n


def _positive_dpi(raw: str) -> int:
    """argparse type for --image-dpi. Reject unreasonably large or
    non-positive values that would trigger a memory-exhaustion DoS in
    rasterize_page()."""
    try:
        n = int(raw)
    except ValueError as e:
        msg = f"invalid int: {raw!r}"
        raise argparse.ArgumentTypeError(msg) from e
    if n < 1:
        msg = f"--image-dpi must be >= 1 (got {n})"
        raise argparse.ArgumentTypeError(msg)
    if n > _MAX_IMAGE_DPI:
        msg = (
            f"--image-dpi capped at {_MAX_IMAGE_DPI} (got {n}); higher "
            "values can exceed addressable memory on realistic page sizes"
        )
        raise argparse.ArgumentTypeError(msg)
    return n


# Exit codes per SPEC.md §2.2. Kept in sync with the CompressError class tree.
EXIT_OK = 0
EXIT_NOOP_PASSTHROUGH = 2
EXIT_ENCRYPTED = 10
EXIT_SIGNED = 11
EXIT_OVERSIZE = 12
EXIT_CORRUPT = 13
EXIT_MALICIOUS = 14
EXIT_CERTIFIED_SIG = 15
EXIT_DECOMPRESSION_BOMB = 16
EXIT_VERIFIER_FAIL = 20
EXIT_ENGINE_ERROR = 30
EXIT_USAGE = 40


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hankpdf",
        description="HankPDF — shrink scanned PDFs locally. No network, no telemetry.",
    )
    p.add_argument("input", nargs="?", type=Path, help='Input PDF path; "-" for stdin')
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        help='Output PDF path; "-" for stdout',
    )
    p.add_argument("-V", "--version", action="store_true", help="Print version and exit")
    p.add_argument("--doctor", action="store_true", help="Print environment report and exit")

    # Engine knobs
    p.add_argument("--mode", choices=["fast", "standard", "safe"], default="standard")
    p.add_argument("--target-bg-dpi", type=int, default=150)
    p.add_argument("--target-color-quality", type=int, default=55)
    p.add_argument("--legal-mode", action="store_true", help="Force CCITT G4 (BSI/NARA profile)")
    p.add_argument("--target-pdfa", action="store_true", help="Target PDF/A-2u output")
    p.add_argument(
        "--force-monochrome",
        action="store_true",
        help=(
            "Collapse mixed/photo pages to the text-only route. Emits "
            "page-N-color-detected-in-monochrome-mode warnings when color "
            "content is flattened. See SPEC.md §2.1."
        ),
    )
    p.add_argument(
        "--bg-codec",
        choices=["jpeg", "jpeg2000"],
        default="jpeg",
        help=(
            "Background codec. Default: jpeg. jpeg2000 is ~10-20%% smaller on "
            "paper textures but adds ~1-2 s/page (demoted to jpeg in fast mode)."
        ),
    )
    p.add_argument(
        "--bg-chroma",
        choices=["4:4:4", "4:2:2", "4:2:0"],
        default="4:4:4",
        help=(
            "Chroma subsampling for bg JPEG. 4:4:4 preserves colored text; "
            "4:2:0 is smaller but smears color on thin strokes. Default: 4:4:4."
        ),
    )

    # OCR — see CompressOptions docstring for the full semantics.
    # Default: existing text layer is preserved verbatim (no Tesseract).
    # --ocr: ensure output is searchable; run Tesseract only if native text
    #        is missing or fails the quality heuristic.
    # --strip-text-layer: explicitly drop any text layer (size-only workflow).
    # --re-ocr: force Tesseract even when the input has a usable text layer.
    p.add_argument("--ocr", dest="ocr", action="store_true", default=False)
    p.add_argument("--no-ocr", dest="ocr", action="store_false")
    p.add_argument("--ocr-language", default="eng")
    p.add_argument("--strip-text-layer", action="store_true", default=False)
    p.add_argument("--re-ocr", action="store_true", default=False)
    p.add_argument(
        "--per-page-min-image-fraction",
        type=float,
        default=0.30,
        help=(
            "Per-page MRC gate threshold. Pages with image_xobject_bytes / "
            "page_byte_budget below this fraction are copied verbatim instead "
            "of recompressed. Default 0.30 — catches native-export PDFs and "
            "skips them. Set to 0.0 to force the full MRC pipeline on every "
            "page."
        ),
    )

    # Safety gates
    p.add_argument("--allow-signed-invalidation", action="store_true")
    p.add_argument("--allow-certified-invalidation", action="store_true")
    p.add_argument("--allow-embedded-files", action="store_true")
    p.add_argument("--password-file", type=Path, help="Read password from file")

    # Limits + passthrough floors
    p.add_argument("--max-pages", type=int, default=10000)
    p.add_argument("--max-input-mb", type=float, default=250.0)
    p.add_argument(
        "--min-input-mb",
        type=float,
        default=0.0,
        help=(
            "Skip compression (return input unchanged) when the input is "
            "smaller than this many MB. Default 0 (gate disabled). Emits a "
            "'passthrough-min-input-mb' warning in the report."
        ),
    )
    p.add_argument(
        "--min-ratio",
        type=float,
        default=1.5,
        help=(
            "If realized compression ratio is below this, return the input "
            "unchanged rather than a larger output. Default 1.5 (matches "
            "CompressOptions default). Set to 0 to disable. Emits a "
            "'passthrough-ratio-floor' warning in the report."
        ),
    )
    p.add_argument(
        "--max-output-mb",
        type=_positive_mb_value,
        default=None,
        help=(
            "Cap the output PDF size. If the compressed output exceeds this "
            "value, split into multiple files named {base}_001{ext}, "
            "{base}_002{ext}, ... (zero-padded, 1-indexed) preserving page "
            "order. Useful for email attachment limits, archival chunk "
            "sizes, etc. A single page that's already larger than the cap "
            "is emitted alone (you'll see a warning)."
        ),
    )
    p.add_argument(
        "--pages",
        type=str,
        default=None,
        help=(
            "Restrict processing to a subset of pages. 1-indexed. Accepts "
            "comma-separated single pages and ranges, e.g. "
            "'1,3-5,10' or '1-3' or '5'. Output PDF contains only the "
            "selected pages in their original order. Useful for smoke tests."
        ),
    )
    p.add_argument(
        "--accept-drift",
        action="store_true",
        help=(
            "Write the output PDF even if the content-preservation verifier "
            "flags drift. Keeps the full-quality (300 DPI source) pipeline, "
            "unlike --mode fast which also lowers DPI. Drift is recorded in "
            "report.warnings. Use only after visually verifying the output."
        ),
    )
    # --verify / --skip-verify: content-drift verifier. Off by default (skipped
    # for speed). --verify re-enables it (with --accept-drift controlling
    # whether drift aborts or just warns). --skip-verify is retained as a
    # no-op for backward compatibility — it matches the new default behavior.
    verify_group = p.add_mutually_exclusive_group()
    verify_group.add_argument(
        "--verify",
        action="store_true",
        help=(
            "Enable the content-drift verifier (off by default since v0.0.x). "
            "Re-rasterizes the output, re-runs OCR, compares against input. "
            "Adds ~2-5 s/page. Use for clinical / legal / archival runs "
            "where post-hoc content-preservation proof matters. Drift "
            "behavior is controlled by --accept-drift (default: abort)."
        ),
    )
    verify_group.add_argument(
        "--skip-verify",
        action="store_true",
        help=argparse.SUPPRESS,  # retained as alias; default is to skip
    )
    p.add_argument(
        "--max-workers",
        type=_max_workers_value,
        default=0,
        help=(
            f"Per-page parallelism. 0 (default) = auto (cpu_count-1, min 1). "
            f"1 = serial. 2..{_MAX_WORKER_COUNT} = exactly N workers. Each "
            "worker gets its own single-page PDF slice, never the whole source."
        ),
    )
    p.add_argument(
        "--per-page-timeout-seconds",
        type=_positive_int,
        default=120,
        help=(
            "Per-page wall-clock budget for rasterize+OCR+compose+verify. "
            "On overrun, the page's Tesseract subprocess is killed and "
            "PerPageTimeoutError is raised. Default: 120."
        ),
    )
    p.add_argument(
        "--total-timeout-seconds",
        type=_positive_int,
        default=1200,
        help=(
            "Total wall-clock budget for the whole compress() call. On "
            "overrun, TotalTimeoutError is raised between pipeline phases. "
            "Default: 1200 (20 min)."
        ),
    )

    # Reporting
    p.add_argument("--report", choices=["text", "json", "jsonl", "none"], default="text")
    p.add_argument("--quiet", action="store_true")

    # Image export mode (JPEG / PNG per page — skips the MRC pipeline).
    p.add_argument(
        "--output-format",
        choices=["pdf", "jpeg", "png", "webp"],
        default=None,
        help=(
            "Output format. Default: inferred from -o extension (.pdf, .jpg/"
            ".jpeg, .png, .webp) or 'pdf' if unknown. Selecting jpeg/png/webp "
            "switches to image-export mode — each selected page is rendered "
            "and encoded as a standalone image file (no MRC compression, no "
            "verifier, no OCR). Use --pages to select a subset."
        ),
    )
    p.add_argument(
        "--image-dpi",
        type=_positive_dpi,
        default=150,
        help=f"DPI for image-export formats. Default: 150. 300 for archival. Max: {_MAX_IMAGE_DPI}.",
    )
    p.add_argument(
        "--jpeg-quality",
        type=int,
        default=75,
        help="JPEG quality 0-100. Default: 75.",
    )
    p.add_argument(
        "--png-compress-level",
        type=int,
        default=6,
        choices=range(10),
        help=(
            "PNG zlib compression level 0-9. 0=no compression, 9=max. Default: 6 (Pillow standard)."
        ),
    )
    p.add_argument(
        "--webp-quality",
        type=int,
        default=80,
        help=(
            "WebP quality 0-100. With --webp-lossless this controls encoder "
            "effort rather than fidelity. Default: 80."
        ),
    )
    p.add_argument(
        "--webp-lossless",
        action="store_true",
        help=(
            "Encode WebP losslessly (bigger file, pixel-exact decode). "
            "Default: lossy WebP at --webp-quality."
        ),
    )
    return p


def _int_from_pages_token(raw: str, part: str) -> int:
    """Parse a token inside a --pages spec, re-raising ValueError with
    --pages context on failure.

    Without this wrapper, Python's built-in ``int("abc")`` surfaces as
    ``invalid literal for int() with base 10: 'abc'`` — no reference to
    --pages, so batch scripts grepping for flag errors miss it
    (Reviewer B).
    """
    try:
        return int(raw)
    except ValueError as exc:
        # Preserve the original text in the message so operators can see
        # WHICH token failed; chain the original via `from exc` for
        # programmatic inspection.
        msg = (
            f"--pages token {part!r} is malformed: expected 1-indexed "
            f"integers separated by ',' and ranges 'lo-hi'; got non-integer "
            f"part {raw!r}"
        )
        raise ValueError(msg) from exc


def _parse_pages_spec(spec: str) -> set[int]:
    """Parse a 1-indexed page spec like '1,3-5,10' into a set of ints.

    Raises ValueError on malformed input. Error messages always reference
    --pages so batch logs are greppable — see :func:`_int_from_pages_token`.
    """
    out: set[int] = set()
    for raw in spec.split(","):
        part = raw.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, hi_s = part.split("-", 1)
            # Empty halves ('-5' -> lo_s='', hi_s='5'; '5-' -> hi_s='') also
            # trip the int() wrap; route through the helper so the error
            # names --pages rather than raising 'invalid literal … :'.
            lo = _int_from_pages_token(lo_s, part)
            hi = _int_from_pages_token(hi_s, part)
            if lo < 1 or hi < lo:
                msg = f"--pages range {part!r} invalid: must be 1-indexed, lo <= hi"
                raise ValueError(msg)
            if hi - lo + 1 > _MAX_PAGES_RANGE:
                msg = (
                    f"--pages range {part!r} too large: cap is "
                    f"{_MAX_PAGES_RANGE:,} pages per range to prevent "
                    "memory exhaustion"
                )
                raise ValueError(msg)
            out.update(range(lo, hi + 1))
        else:
            n = _int_from_pages_token(part, part)
            if n < 1:
                msg = f"--pages value {part!r} invalid: must be 1-indexed"
                raise ValueError(msg)
            out.add(n)
    # Total cardinality cap — a per-range check alone allows
    # `--pages 1-1000000,2000001-3000000,…` to accumulate unbounded.
    if len(out) > _MAX_PAGES_RANGE:
        msg = (
            f"--pages total set too large: {len(out):,} pages; cap is "
            f"{_MAX_PAGES_RANGE:,} total pages to prevent memory exhaustion"
        )
        raise ValueError(msg)
    return out


def _read_password(args: argparse.Namespace) -> str | None:
    if args.password_file is not None:
        content = args.password_file.read_text(encoding="utf-8")
        # Strip exactly one trailing newline (CR, LF, or CRLF). Don't use
        # .strip() — that would also eat leading/trailing spaces inside the
        # password itself.
        if content.endswith("\r\n"):
            content = content[:-2]
        elif content.endswith(("\n", "\r")):
            content = content[:-1]
        return content or None
    return os.environ.get("HANKPDF_PASSWORD")


def _build_options(args: argparse.Namespace) -> CompressOptions:
    return CompressOptions(
        mode=args.mode,
        bg_codec=args.bg_codec,
        bg_chroma_subsampling=args.bg_chroma,
        target_bg_dpi=args.target_bg_dpi,
        target_color_quality=args.target_color_quality,
        force_monochrome=args.force_monochrome,
        accept_drift=args.accept_drift,
        # Verifier is OFF by default (skip_verify=True). --verify opts in.
        # --skip-verify still accepted as a no-op alias for the default.
        skip_verify=not args.verify,
        max_workers=args.max_workers,
        legal_codec_profile="ccitt-g4" if args.legal_mode else None,
        target_pdf_a=args.target_pdfa,
        ocr=args.ocr,
        ocr_language=args.ocr_language,
        strip_text_layer=args.strip_text_layer,
        re_ocr=args.re_ocr,
        min_image_byte_fraction=args.per_page_min_image_fraction,
        allow_signed_invalidation=args.allow_signed_invalidation,
        allow_certified_invalidation=args.allow_certified_invalidation,
        allow_embedded_files=args.allow_embedded_files,
        password=_read_password(args),
        max_pages=args.max_pages,
        max_input_mb=args.max_input_mb,
        min_input_mb=args.min_input_mb,
        min_ratio=args.min_ratio,
        per_page_timeout_seconds=args.per_page_timeout_seconds,
        total_timeout_seconds=args.total_timeout_seconds,
    )


def _doctor_report() -> str:
    import platform
    import shutil
    import subprocess

    from hankpdf._version import build_info, version_line

    lines = [
        version_line(),
        f"platform {platform.platform()}",
    ]
    _info = build_info()
    if _info is not None:
        lines.append("build info (from /etc/hankpdf/build-info.json):")
        for key in (
            "git_sha",
            "build_date",
            "base_image_digest",
            "jbig2enc_commit",
            "qpdf_version",
            "tesseract_version",
            "leptonica_version",
            "python_version",
            "os_platform",
        ):
            val = _info.get(key, "?")
            lines.append(f"  {key:20s} {val}")
    for tool in ("tesseract", "qpdf", "jbig2"):
        path = shutil.which(tool)
        if path:
            try:
                out = subprocess.run(
                    [tool, "--version"],
                    capture_output=True,
                    check=False,
                    timeout=5,
                    text=True,
                )
                first_line = (out.stdout or out.stderr).splitlines()[:1]
                ver = first_line[0] if first_line else "unknown"
            except subprocess.TimeoutExpired, OSError:
                ver = "unreachable"
            lines.append(f"  {tool:12s} {ver}")
        else:
            lines.append(f"  {tool:12s} NOT FOUND")

    # JPEG2000 via Pillow's bundled OpenJPEG — probe by attempting encode.
    # A Pillow wheel built without OpenJPEG silently falls back to JPEG on
    # the bg_codec=jpeg2000 path; surface that here so users can diagnose.
    try:
        import io as _io_probe

        from PIL import Image as _PILImage

        _buf = _io_probe.BytesIO()
        _PILImage.new("RGB", (8, 8)).save(_buf, format="JPEG2000")
        lines.append(f"  {'JPEG2000':12s} available (Pillow/OpenJPEG)")
    except (OSError, ImportError) as e:
        lines.append(
            f"  {'JPEG2000':12s} UNAVAILABLE ({type(e).__name__}) — "
            "bg_codec=jpeg2000 will fall back to JPEG"
        )

    # jbig2enc presence: absence drops text-only ratio from ~50x to ~6x.
    if shutil.which("jbig2") is None:
        lines.append(
            f"  {'jbig2enc':12s} NOT FOUND — text-only compression will fall "
            "back to flate (~6x reduction vs ~50x with jbig2)"
        )
    else:
        lines.append(f"  {'jbig2enc':12s} available")
    return "\n".join(lines)


def _write_correlation_sidecar(
    output_path: Path,
    report: CompressReport,
    run_id: str,
) -> None:
    """Write ``{output_stem}_correlation.json`` next to the compressed output.

    Wave 5 / C3: the file maps the process-wide correlation_id (also
    stamped on every stderr line) to the input SHA-256 so an on-call
    can tie a batch-log slice back to the input without us ever
    recording the input filename in plaintext.

    Shape (single-entry — batched CLI invocations are a future
    ROADMAP item, but the schema is already array-shaped for forward
    compatibility):

    .. code-block:: json

        {
            "run_id": "<uuid4-hex>",
            "started_at": "<iso-8601-utc>",
            "build_info": { ... } | null,
            "entries": [
                {
                    "correlation_id": "<uuid4-hex-first-8>",
                    "input_sha256": "sha256:<hex>",
                    "input_size": 12345,
                    "exit_code": 0,
                    "output_path": "out.pdf"
                }
            ]
        }

    Failures to write the sidecar are swallowed — the output PDF is the
    user's primary artifact; a missing sidecar only degrades the
    on-call recovery workflow, not the correctness of the output.
    """
    import dataclasses
    import datetime

    sidecar_path = output_path.parent / f"{output_path.stem}_correlation.json"
    try:
        build_info_dict: dict[str, str] | None = None
        if report.build_info is not None:
            build_info_dict = dataclasses.asdict(report.build_info)
        payload = {
            "run_id": run_id,
            "started_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "build_info": build_info_dict,
            "entries": [
                {
                    "correlation_id": report.correlation_id,
                    "input_sha256": f"sha256:{report.input_sha256}",
                    "input_size": report.input_bytes,
                    "exit_code": report.exit_code,
                    "output_path": output_path.name,
                },
            ],
        }
        _atomic_write_bytes(sidecar_path, json.dumps(payload, indent=2).encode("utf-8"))
    except OSError:
        # Sidecar write failed (disk full, permissions). The main output
        # already landed; surface a warning but don't fail the run.
        print(
            f"[hankpdf] warning: could not write correlation sidecar to "
            f"{sidecar_path}; on-call recovery via correlation_id may be harder",
            file=sys.stderr,
        )


def _format_report(report, fmt: str) -> str:  # type: ignore[no-untyped-def]
    if fmt == "none":
        return ""
    if fmt in {"json", "jsonl"}:
        payload = asdict(report)
        # Enums / VerifierResult need flattening; asdict handles frozen dataclasses.
        return json.dumps(payload, default=str)
    # Plain text
    pct = (report.output_bytes / max(1, report.input_bytes)) * 100
    v_status = report.verifier.status
    suffix = f" verifier={v_status}"
    if report.warnings:
        suffix += f" warnings={len(report.warnings)}"
    return (
        f"ok  {report.input_bytes:,} -> {report.output_bytes:,} bytes "
        f"({pct:.1f}%, ratio {report.ratio:.2f}x, {report.pages} pages, "
        f"{report.wall_time_ms} ms){suffix}"
    )


def _run_image_export(
    args: argparse.Namespace,
    input_bytes: bytes,
    only_pages: set[int] | None,
    image_format: str,
) -> int:
    """Image-export path: skip the MRC/compress pipeline, render each
    requested page as JPEG/PNG. Invoked from main() when the user picks
    jpeg/png via --output-format or an image extension on -o.
    """
    _label = _input_label(args.input)
    _prefix = _line_prefix(_label)
    password = _read_password(args)

    # --max-output-mb is a PDF-only concept (it splits a merged PDF into
    # size-bounded sibling files). In image-export mode each page is
    # already its own file, so the flag has no semantics here. Warn
    # loudly rather than silently ignore.
    if args.max_output_mb is not None and not args.quiet:
        print(
            _warn(
                "W-MAX-OUTPUT-MB-IMAGE-MODE",
                "--max-output-mb applies only to PDF output; ignored in image-export mode",
                input_name=_label,
            ),
            file=sys.stderr,
        )

    # Determine total page count via a triage. Route specific
    # CompressError subclasses to their own exit codes BEFORE the
    # generic catch-all, so upstream can distinguish a decompression
    # bomb or malicious PDF from a plain corrupt one.
    try:
        tri = triage(input_bytes, password=password)
    except MaliciousPDFError as e:
        print(
            _refuse("E-INPUT-MALICIOUS", f"malicious PDF ({e})", input_name=_label), file=sys.stderr
        )
        return EXIT_MALICIOUS
    except DecompressionBombError as e:
        print(
            _refuse("E-INPUT-DECOMPRESSION-BOMB", f"decompression bomb ({e})", input_name=_label),
            file=sys.stderr,
        )
        return EXIT_DECOMPRESSION_BOMB
    except CompressError as e:
        print(_refuse("E-INPUT-CORRUPT", str(e), input_name=_label), file=sys.stderr)
        return EXIT_CORRUPT

    # Enforce the same safety gates as compress(). Encrypted/signed/oversize
    # PDFs must be refused regardless of output format.
    try:
        _enforce_input_policy(tri, _build_options(args), input_bytes)
    except EncryptedPDFError as e:
        print(
            _refuse("E-INPUT-ENCRYPTED", f"encrypted without password ({e})", input_name=_label),
            file=sys.stderr,
        )
        return EXIT_ENCRYPTED
    except CertifiedSignatureError as e:
        print(
            _refuse("E-INPUT-CERTIFIED", f"certifying signature ({e})", input_name=_label),
            file=sys.stderr,
        )
        return EXIT_CERTIFIED_SIG
    except SignedPDFError as e:
        print(_refuse("E-INPUT-SIGNED", f"signed PDF ({e})", input_name=_label), file=sys.stderr)
        return EXIT_SIGNED
    except OversizeError as e:
        print(_refuse("E-INPUT-OVERSIZE", f"oversize ({e})", input_name=_label), file=sys.stderr)
        return EXIT_OVERSIZE

    if only_pages is not None:
        out_of_range = [p for p in only_pages if p < 1 or p > tri.pages]
        if out_of_range:
            print(
                f"error: --pages requested {format_page_list_short(out_of_range)} "
                f"but input has {tri.pages} pages",
                file=sys.stderr,
            )
            return EXIT_USAGE
        page_indices = sorted(p - 1 for p in only_pages)
    else:
        page_indices = list(range(tri.pages))

    if not page_indices:
        print(
            "error: --pages parsed to an empty set (no pages selected); "
            "provide at least one 1-indexed page number",
            file=sys.stderr,
        )
        return EXIT_USAGE

    # Stdout (-o -) only supports a single image. Check up front so we
    # don't spin up rasterize just to reject after the fact.
    n = len(page_indices)
    if str(args.output) == "-" and n != 1:
        print(
            f"error: -o - (stdout) supports exactly one image; "
            f"got {n} (use --pages to select a single page)",
            file=sys.stderr,
        )
        return EXIT_USAGE

    if str(args.output) != "-":
        args.output.parent.mkdir(parents=True, exist_ok=True)

    out_ext = {"jpeg": ".jpg", "png": ".png", "webp": ".webp"}[image_format]
    # Map each valid image ext to its canonical image_format to detect
    # mismatches (e.g. -o out.jpeg --output-format png must NOT write
    # PNG bytes to a .jpeg file).
    ext_to_format = {
        "jpg": "jpeg",
        "jpeg": "jpeg",
        "png": "png",
        "webp": "webp",
    }
    base = args.output.stem
    parent = args.output.parent
    # Keep the user's image extension only if it matches the resolved
    # format; else replace it with the canonical one for image_format.
    requested_ext = args.output.suffix.lower()
    ext_matches_format = ext_to_format.get(requested_ext.lstrip(".")) == image_format
    final_ext = requested_ext if ext_matches_format else out_ext

    # Progress: tqdm bar ticks on each encoded page, so a 400-page PNG
    # export shows real progress (was silent while ~8 GB buffered in
    # memory pre-Task-8). --quiet suppresses it.
    from tqdm import tqdm  # type: ignore[import-untyped]

    _bar: tqdm | None = None
    if not args.quiet:
        _bar = tqdm(
            total=n,
            desc=f"{image_format}",
            unit="pg",
            file=sys.stderr,
            dynamic_ncols=True,
            leave=True,
        )

    def _progress(phase: str, _current: int, _total: int) -> None:
        if _bar is not None and phase == "page_done":
            _bar.update(1)

    try:
        pages_iter = iter_pages_as_images(
            input_bytes,
            page_indices=page_indices,
            image_format=image_format,  # type: ignore[arg-type]
            dpi=args.image_dpi,
            jpeg_quality=args.jpeg_quality,
            png_compress_level=args.png_compress_level,
            webp_quality=args.webp_quality,
            webp_lossless=args.webp_lossless,
            progress_callback=_progress,
            password=password,
        )

        if n == 1:
            # Single-page fast path: either stream to stdout or write to
            # the (possibly-ext-normalized) -o target.
            blob = next(iter(pages_iter))
            if str(args.output) == "-":
                sys.stdout.buffer.write(blob)
                return EXIT_OK
            target = args.output if ext_matches_format else parent / f"{base}{final_ext}"
            _atomic_write_bytes(target, blob)
            if not args.quiet:
                print(
                    f"{_prefix} wrote {target} ({len(blob):,} bytes, "
                    f"{image_format} @ {args.image_dpi} DPI)",
                    file=sys.stderr,
                )
        else:
            total_bytes = 0
            written_paths: list[Path] = []
            # Scale pad width so 1200-page exports don't mix 3- and 4-digit
            # filenames (which sort-lex wrong: out_100 < out_99). The width
            # tracks both count AND the largest page number we might print,
            # since --pages can select a sparse range.
            largest_num = max(page_indices) + 1 if page_indices else 1
            pad_w = max(3, len(str(n)), len(str(largest_num)))
            try:
                for page_idx, blob in zip(page_indices, pages_iter, strict=True):
                    target = parent / f"{base}_{page_idx + 1:0{pad_w}d}{final_ext}"
                    _atomic_write_bytes(target, blob)
                    written_paths.append(target)
                    total_bytes += len(blob)
                    if not args.quiet:
                        print(
                            f"{_prefix} wrote {target.name} "
                            f"({len(blob):,} bytes, page {page_idx + 1})",
                            file=sys.stderr,
                        )
            except MaliciousPDFError as exc:
                print(
                    _warn_error(
                        "W-IMAGE-EXPORT-PARTIAL-FAILURE",
                        f"image export failed after writing {len(written_paths)}/{n} pages: {exc}",
                        input_name=_label,
                    ),
                    file=sys.stderr,
                )
                if written_paths:
                    print(
                        f"{_prefix} wrote these before failure: {[p.name for p in written_paths]}",
                        file=sys.stderr,
                    )
                return EXIT_MALICIOUS
            except DecompressionBombError as exc:
                print(
                    _warn_error(
                        "W-IMAGE-EXPORT-PARTIAL-FAILURE",
                        f"image export failed after writing {len(written_paths)}/{n} pages: {exc}",
                        input_name=_label,
                    ),
                    file=sys.stderr,
                )
                if written_paths:
                    print(
                        f"{_prefix} wrote these before failure: {[p.name for p in written_paths]}",
                        file=sys.stderr,
                    )
                return EXIT_DECOMPRESSION_BOMB
            except (RuntimeError, CompressError) as exc:
                print(
                    _warn_error(
                        "W-IMAGE-EXPORT-PARTIAL-FAILURE",
                        f"image export failed after writing {len(written_paths)}/{n} pages: {exc}",
                        input_name=_label,
                    ),
                    file=sys.stderr,
                )
                if written_paths:
                    print(
                        f"{_prefix} wrote these before failure: {[p.name for p in written_paths]}",
                        file=sys.stderr,
                    )
                return EXIT_ENGINE_ERROR
            if not args.quiet:
                print(
                    f"{_prefix} exported {n} {image_format} pages "
                    f"({total_bytes:,} total bytes, {args.image_dpi} DPI)",
                    file=sys.stderr,
                )
    finally:
        if _bar is not None:
            _bar.close()
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)

    # Generate a correlation id for this invocation and register it with
    # the audit module so every warning_codes stderr line gets stamped.
    # Wave 5 / C2: on-call can grep a batch log for `corr=<id>` and tie
    # the log slice back to the structured CompressReport whose
    # `correlation_id` matches.
    import uuid as _uuid

    from hankpdf.audit import set_correlation_id

    _run_correlation_id = _uuid.uuid4().hex
    set_correlation_id(_run_correlation_id)

    if args.version:
        # version_line() embeds git_sha + build_date + image digest when
        # running from a Docker image that wrote /etc/hankpdf/build-info.json
        # at build time (B3). Dev installs just print version + python.
        from hankpdf._version import version_line

        print(version_line())
        return EXIT_OK

    if args.doctor:
        print(_doctor_report())
        return EXIT_OK

    if args.input is None or args.output is None:
        print("error: INPUT and -o/--output are required (or pass --doctor)", file=sys.stderr)
        return EXIT_USAGE

    # Read input
    if str(args.input) == "-":
        input_bytes = sys.stdin.buffer.read()
    else:
        size_mb = args.input.stat().st_size / (1024 * 1024)
        if size_mb > args.max_input_mb:
            print(
                _refuse(
                    "E-INPUT-OVERSIZE",
                    f"input is {size_mb:.1f} MB, exceeds --max-input-mb={args.max_input_mb} "
                    f"(default tightened from 2000.0; pass --max-input-mb 2000 to restore "
                    f"the previous behavior)",
                    input_name=_input_label(args.input),
                ),
                file=sys.stderr,
            )
            return EXIT_OVERSIZE
        input_bytes = args.input.read_bytes()

    # Resolve the log-label once so every stderr line this run emits
    # carries the same redacted filename prefix (per THREAT_MODEL.md §5).
    # stdin returns None → plain "[hankpdf]" prefix; no empty hash.
    _label = _input_label(args.input)
    _prefix = _line_prefix(_label)

    options = _build_options(args)

    only_pages: set[int] | None = None
    if args.pages is not None:
        try:
            only_pages = _parse_pages_spec(args.pages)
        except ValueError as e:
            print(f"error: --pages {e}", file=sys.stderr)
            return EXIT_USAGE
        # Empty spec (e.g., --pages "" from env-var expansion) must be
        # treated as a usage error regardless of output format. Without
        # this shared guard, the PDF path used to fall through to
        # compress() → CompressError → EXIT_ENGINE_ERROR=30, while the
        # image-export path returned EXIT_USAGE=40. Unify.
        if not only_pages:
            print(
                "error: --pages parsed to an empty set (no pages "
                "selected); provide at least one 1-indexed page number",
                file=sys.stderr,
            )
            return EXIT_USAGE

    # Resolve output format. --output-format wins; otherwise infer from
    # the output file extension; default to pdf.
    if args.output_format is not None:
        resolved_format = args.output_format
    else:
        ext = args.output.suffix.lower().lstrip(".") if args.output else ""
        resolved_format = {
            "jpg": "jpeg",
            "jpeg": "jpeg",
            "png": "png",
            "webp": "webp",
        }.get(ext, "pdf")

    # Warn if an explicit --output-format overrides the -o extension.
    # Users expect -o out.pdf to produce a PDF; if they also pass
    # --output-format jpeg we silently wrote out.jpg with no explanation
    # for the rename. Surface the override. DCR Wave 1.
    if args.output_format is not None and args.output is not None:
        o_ext = args.output.suffix.lower().lstrip(".")
        implicit_format = {
            "jpg": "jpeg",
            "jpeg": "jpeg",
            "png": "png",
            "webp": "webp",
            "pdf": "pdf",
        }.get(o_ext)
        if implicit_format is not None and implicit_format != resolved_format and not args.quiet:
            print(
                _warn(
                    "W-OUTPUT-FORMAT-EXTENSION-OVERRIDE",
                    f"--output-format {resolved_format} overrides the "
                    f".{o_ext} extension; output will be written as "
                    f"{resolved_format} regardless of the filename suffix",
                    input_name=_label,
                ),
                file=sys.stderr,
            )

    # Image-export mode bypasses the MRC pipeline entirely.
    if resolved_format in {"jpeg", "png", "webp"}:
        return _run_image_export(args, input_bytes, only_pages, resolved_format)

    # Progress: tqdm bar for the per-page phase + plain stderr lines for
    # the triage/merge/verify milestones. All output goes to stderr so that
    # --report json on stdout stays clean for piping. --quiet suppresses both.
    from tqdm import tqdm

    from hankpdf import ProgressEvent

    _bar: tqdm | None = None

    def _progress(event: ProgressEvent) -> None:
        nonlocal _bar
        if args.quiet:
            return
        if event.phase == "triage_complete":
            print(f"{_prefix} {event.message}", file=sys.stderr, flush=True)
            # Create the per-page bar up front with total page count.
            _bar = tqdm(
                total=event.total,
                desc="pages",
                unit="pg",
                file=sys.stderr,
                dynamic_ncols=True,
                leave=True,
            )
        elif event.phase == "page_start" and _bar is not None:
            _bar.set_postfix_str(f"rasterizing p{event.current}")
        elif event.phase == "page_done" and _bar is not None:
            # Tri-state: True=pass, False=fail, None=verifier didn't run
            # (skip_verify). Surface each distinctly — don't collapse
            # None into "FAIL" since nothing was actually verified.
            if event.verifier_passed is True:
                tag = "pass"
            elif event.verifier_passed is False:
                tag = "FAIL"
            else:
                tag = "skip"
            ratio_str = f"{event.ratio:.2f}x" if event.ratio else "?x"
            byte_str = (
                f"{event.input_bytes // 1024}→{event.output_bytes // 1024}KB"
                if event.input_bytes and event.output_bytes
                else ""
            )
            _bar.set_postfix_str(f"{event.strategy} {byte_str} {ratio_str} {tag}")
            _bar.update(1)
            # On failure, tqdm.write a permanent line above the bar so the
            # user can see *which* page failed without losing the bar.
            if event.verifier_passed is False:
                _bar.write(
                    f"  ⚠ page {event.current}/{event.total} "
                    f"({event.strategy}, {byte_str} {ratio_str}): verifier FAIL",
                )
        elif event.phase == "merge_start":
            if _bar is not None:
                _bar.close()
                _bar = None
            print(f"{_prefix} {event.message}", file=sys.stderr, flush=True)
        elif event.phase in {"merge_complete", "triage"}:
            print(f"{_prefix} {event.message}", file=sys.stderr, flush=True)

    try:
        try:
            output_bytes, report = compress(
                input_bytes,
                options=options,
                progress_callback=_progress,
                only_pages=only_pages,
                correlation_id=_run_correlation_id,
            )
        except EncryptedPDFError as e:
            print(
                _refuse(
                    "E-INPUT-ENCRYPTED", f"encrypted without password ({e})", input_name=_label
                ),
                file=sys.stderr,
            )
            return EXIT_ENCRYPTED
        except CertifiedSignatureError as e:
            print(
                _refuse("E-INPUT-CERTIFIED", f"certifying signature ({e})", input_name=_label),
                file=sys.stderr,
            )
            return EXIT_CERTIFIED_SIG
        except SignedPDFError as e:
            print(
                _refuse("E-INPUT-SIGNED", f"signed PDF ({e})", input_name=_label), file=sys.stderr
            )
            return EXIT_SIGNED
        except OversizeError as e:
            print(
                _refuse("E-INPUT-OVERSIZE", f"oversize ({e})", input_name=_label), file=sys.stderr
            )
            return EXIT_OVERSIZE
        except DecompressionBombError as e:
            print(
                _refuse(
                    "E-INPUT-DECOMPRESSION-BOMB", f"decompression bomb ({e})", input_name=_label
                ),
                file=sys.stderr,
            )
            return EXIT_DECOMPRESSION_BOMB
        except CorruptPDFError as e:
            print(
                _refuse("E-INPUT-CORRUPT", f"corrupt PDF ({e})", input_name=_label), file=sys.stderr
            )
            return EXIT_CORRUPT
        except MaliciousPDFError as e:
            print(
                _refuse("E-INPUT-MALICIOUS", f"malicious PDF ({e})", input_name=_label),
                file=sys.stderr,
            )
            return EXIT_MALICIOUS
        except ContentDriftError as e:
            print(
                _warn_error("E-VERIFIER-FAIL", f"aborted: content drift ({e})", input_name=_label),
                file=sys.stderr,
            )
            return EXIT_VERIFIER_FAIL
        # Timeouts — subclasses of CompressError; catch BEFORE the generic
        # handler so they carry stable codes (exit code is EXIT_ENGINE_ERROR
        # since timeouts aren't a separate code in the SPEC §2.2 table).
        except OcrTimeoutError as e:
            print(
                _warn_error("E-OCR-TIMEOUT", f"OCR subprocess timed out ({e})", input_name=_label),
                file=sys.stderr,
            )
            return EXIT_ENGINE_ERROR
        except PerPageTimeoutError as e:
            print(
                _warn_error("E-TIMEOUT-PER-PAGE", f"per-page timeout ({e})", input_name=_label),
                file=sys.stderr,
            )
            return EXIT_ENGINE_ERROR
        except TotalTimeoutError as e:
            print(
                _warn_error(
                    "E-TIMEOUT-TOTAL", f"total wall-clock timeout ({e})", input_name=_label
                ),
                file=sys.stderr,
            )
            return EXIT_ENGINE_ERROR
        except CompressError as e:
            print(
                _warn_error("E-ENGINE-ERROR", str(e), input_name=_label),
                file=sys.stderr,
            )
            return EXIT_ENGINE_ERROR
    finally:
        if _bar is not None:
            _bar.close()

    # Write output — possibly as multiple chunked files if --max-output-mb set.
    if str(args.output) == "-":
        # Stdout: can't split; always write the merged bytes.
        if args.max_output_mb is not None and len(output_bytes) > args.max_output_mb * 1024 * 1024:
            print(
                _warn(
                    "W-MAX-OUTPUT-MB-STDOUT",
                    "--max-output-mb is ignored when -o - (stdout); wrote merged output",
                    input_name=_label,
                ),
                file=sys.stderr,
            )
        sys.stdout.buffer.write(output_bytes)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.max_output_mb is None:
            _atomic_write_bytes(args.output, output_bytes)
        else:
            max_bytes = int(args.max_output_mb * 1024 * 1024)
            chunks = split_pdf_by_size(output_bytes, max_bytes=max_bytes)
            if len(chunks) == 1:
                _atomic_write_bytes(args.output, chunks[0])
                # Oversize check even on the single-chunk path. This
                # happens when the splitter can't go below the cap
                # (e.g., a single page is already larger than max_bytes)
                # — same condition the multi-chunk branch warns about.
                if len(chunks[0]) > max_bytes and not args.quiet:
                    print(
                        _warn(
                            "W-SINGLE-CHUNK-OVERSIZE",
                            f"output exceeds --max-output-mb cap "
                            f"({len(chunks[0]) / (1024 * 1024):.2f} MB > "
                            f"{args.max_output_mb:.3f} MB). Single-page "
                            "PDFs cannot be split further; the oversize "
                            "output was retained.",
                            input_name=_label,
                        ),
                        file=sys.stderr,
                    )
            else:
                base = args.output.stem
                ext = args.output.suffix
                parent = args.output.parent

                # Pad width scales with chunk count so 1200-chunk jobs
                # produce {base}_0001{ext} ... {base}_1200{ext} rather
                # than a mix of 3- and 4-digit names that sort-lex wrong.
                chunk_pad_w = max(3, len(str(len(chunks))))

                # Detect stale chunks from prior runs that will not be
                # overwritten because our new chunk count is smaller.
                # Matches {base}_NN+{ext} with numeric index > len(chunks).
                # Allow 3+ digits dynamically so a previous 1200-chunk
                # run's _0150 file is still detected as stale by a
                # subsequent 50-chunk run.
                chunk_re = re.compile(
                    rf"^{re.escape(base)}_(\d{{3,}}){re.escape(ext)}$",
                )
                stale: list[Path] = []
                if parent.exists():
                    for existing in parent.iterdir():
                        m = chunk_re.match(existing.name)
                        if m is not None and int(m.group(1)) > len(chunks):
                            stale.append(existing)

                written_paths: list[Path] = []
                try:
                    for idx, chunk in enumerate(chunks, start=1):
                        p = parent / f"{base}_{idx:0{chunk_pad_w}d}{ext}"
                        _atomic_write_bytes(p, chunk)
                        written_paths.append(p)
                except OSError as exc:
                    # Disk full / permission / path errors mid-write.
                    # Orphan whatever shards hit disk before the failure;
                    # tell the operator explicitly rather than leaving a
                    # raw OSError traceback.
                    print(
                        _warn_error(
                            "W-CHUNK-WRITE-PARTIAL-FAILURE",
                            f"chunk write failed after "
                            f"{len(written_paths)}/{len(chunks)} chunks: {exc}",
                            input_name=_label,
                        ),
                        file=sys.stderr,
                    )
                    if written_paths:
                        print(
                            f"{_prefix} wrote these before failure: "
                            f"{[p.name for p in written_paths]}",
                            file=sys.stderr,
                        )
                    return EXIT_ENGINE_ERROR
                oversize = [p for p in written_paths if p.stat().st_size > max_bytes]
                if not args.quiet:
                    print(
                        f"{_prefix} wrote {len(chunks)} chunks "
                        f"({args.max_output_mb:.1f} MB cap); "
                        f"sizes: {[f'{p.stat().st_size / (1024 * 1024):.2f} MB' for p in written_paths]}",
                        file=sys.stderr,
                    )
                    if oversize:
                        print(
                            _warn(
                                "W-CHUNKS-EXCEED-CAP",
                                f"{len(oversize)} chunk(s) exceed the cap "
                                "because they contain a single oversize "
                                f"page: {[p.name for p in oversize]}",
                                input_name=_label,
                            ),
                            file=sys.stderr,
                        )
                # W-STALE-CHUNK-FILES deliberately OUTSIDE the `not args.quiet`
                # guard. Stale chunks are a correctness hazard: downstream
                # batch jobs glob `{base}_*.pdf` and silently ingest chunks
                # from a prior run whose index is higher than the current
                # chunk count. Cron jobs typically run with --quiet, so a
                # quiet-suppressed stale warning would hide exactly the case
                # it matters for. SPEC.md §8.5.1 documents this exception.
                if stale:
                    stale_names = sorted(p.name for p in stale)
                    print(
                        _warn(
                            "W-STALE-CHUNK-FILES",
                            f"{len(stale)} stale chunk file(s) from a "
                            f"previous run remain in {parent}: "
                            f"{stale_names}. Remove them manually if "
                            "they no longer belong to this output.",
                            input_name=_label,
                        ),
                        file=sys.stderr,
                    )

    # Correlation sidecar (Wave 5 / C3). Emits {output_stem}_correlation.json
    # alongside the output whenever output is a file. Maps the run's
    # correlation_id to the input SHA-256 so an on-call can:
    #   1. grep the batch log for `corr=<id>` to find the stderr slice
    #   2. open {output}_correlation.json to get the input_sha256
    #   3. cross-reference with the user's separate input-sha mapping
    #      to identify the original filename (filenames stay redacted
    #      per THREAT_MODEL.md §5).
    # See docs/TROUBLESHOOTING.md for the full recovery playbook.
    if str(args.output) != "-":
        _write_correlation_sidecar(args.output, report, _run_correlation_id)

    # Verifier-status banner. Default skip_verify=True is a UX trap:
    # users see a clean text report and assume the output was content-
    # checked against the input. Surface verifier.status explicitly on
    # stderr when it's not a clean pass. --quiet suppresses the banner
    # along with the rest of the progress chrome.
    if not args.quiet:
        v_status = report.verifier.status
        if v_status == "skipped":
            print(
                _warn(
                    "W-VERIFIER-SKIPPED",
                    "content-preservation verifier was SKIPPED (default). "
                    "Output was NOT content-checked against input. "
                    "Use --verify to enable.",
                    input_name=_label,
                ),
                file=sys.stderr,
            )
        elif v_status == "fail":
            print(
                _warn(
                    "W-VERIFIER-FAILED",
                    "content-preservation verifier FAILED on "
                    f"{len(report.verifier.failing_pages)} page(s)",
                    input_name=_label,
                ),
                file=sys.stderr,
            )

    if not args.quiet and args.report != "none":
        line = _format_report(report, args.report)
        if line:
            # JSON reports go to stdout only when output was a file; when
            # output is stdout, report goes to stderr to avoid mixing.
            stream = sys.stderr if str(args.output) == "-" else sys.stdout
            print(line, file=stream)

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
