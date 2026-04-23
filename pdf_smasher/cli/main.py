"""HankPDF CLI entry point.

See docs/SPEC.md §2 for the full contract: flags, exit codes, report
schema.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from pdf_smasher import (
    CertifiedSignatureError,
    CompressError,
    CompressOptions,
    ContentDriftError,
    CorruptPDFError,
    DecompressionBombError,
    EncryptedPDFError,
    MaliciousPDFError,
    OversizeError,
    SignedPDFError,
    __version__,
    compress,
)

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

    # OCR
    p.add_argument("--ocr", dest="ocr", action="store_true", default=True)
    p.add_argument("--no-ocr", dest="ocr", action="store_false")
    p.add_argument("--ocr-language", default="eng")

    # Safety gates
    p.add_argument("--allow-signed-invalidation", action="store_true")
    p.add_argument("--allow-certified-invalidation", action="store_true")
    p.add_argument("--allow-embedded-files", action="store_true")
    p.add_argument("--password-file", type=Path, help="Read password from file")

    # Limits
    p.add_argument("--max-pages", type=int)
    p.add_argument("--max-input-mb", type=float, default=2000.0)

    # Reporting
    p.add_argument("--report", choices=["text", "json", "jsonl", "none"], default="text")
    p.add_argument("--quiet", action="store_true")
    return p


def _read_password(args: argparse.Namespace) -> str | None:
    if args.password_file is not None:
        return args.password_file.read_text().strip() or None
    return os.environ.get("HANKPDF_PASSWORD")


def _build_options(args: argparse.Namespace) -> CompressOptions:
    return CompressOptions(
        mode=args.mode,
        target_bg_dpi=args.target_bg_dpi,
        target_color_quality=args.target_color_quality,
        force_monochrome=args.force_monochrome,
        legal_codec_profile=args.legal_mode,
        target_pdf_a=args.target_pdfa,
        ocr=args.ocr,
        ocr_language=args.ocr_language,
        allow_signed_invalidation=args.allow_signed_invalidation,
        allow_certified_invalidation=args.allow_certified_invalidation,
        allow_embedded_files=args.allow_embedded_files,
        password=_read_password(args),
        max_pages=args.max_pages,
        max_input_mb=args.max_input_mb,
    )


def _doctor_report() -> str:
    import platform
    import shutil
    import subprocess

    lines = [
        f"hankpdf {__version__}",
        f"python {platform.python_version()}",
        f"platform {platform.platform()}",
    ]
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
            except (subprocess.TimeoutExpired, OSError):
                ver = "unreachable"
            lines.append(f"  {tool:12s} {ver}")
        else:
            lines.append(f"  {tool:12s} NOT FOUND")

    # JPEG2000 via Pillow's bundled OpenJPEG — probe by attempting encode.
    # A Pillow wheel built without OpenJPEG silently falls back to JPEG on
    # the bg_codec=jpeg2000 path; surface that here so users can diagnose.
    try:
        import io as _io_probe

        from PIL import Image as _pil_probe

        _buf = _io_probe.BytesIO()
        _pil_probe.new("RGB", (8, 8)).save(_buf, format="JPEG2000")
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


def _format_report(report, fmt: str) -> str:  # type: ignore[no-untyped-def]
    if fmt == "none":
        return ""
    if fmt in {"json", "jsonl"}:
        payload = asdict(report)
        # Enums / VerifierResult need flattening; asdict handles frozen dataclasses.
        return json.dumps(payload, default=str)
    # Plain text
    pct = (report.output_bytes / max(1, report.input_bytes)) * 100
    return (
        f"ok  {report.input_bytes:,} -> {report.output_bytes:,} bytes "
        f"({pct:.1f}%, ratio {report.ratio:.2f}x, {report.pages} pages, "
        f"{report.wall_time_ms} ms)"
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)

    if args.version:
        print(f"hankpdf {__version__}")
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
        input_bytes = args.input.read_bytes()

    options = _build_options(args)

    try:
        output_bytes, report = compress(input_bytes, options=options)
    except EncryptedPDFError as e:
        print(f"refused: encrypted without password ({e})", file=sys.stderr)
        return EXIT_ENCRYPTED
    except CertifiedSignatureError as e:
        print(f"refused: certifying signature ({e})", file=sys.stderr)
        return EXIT_CERTIFIED_SIG
    except SignedPDFError as e:
        print(f"refused: signed PDF ({e})", file=sys.stderr)
        return EXIT_SIGNED
    except OversizeError as e:
        print(f"refused: oversize ({e})", file=sys.stderr)
        return EXIT_OVERSIZE
    except DecompressionBombError as e:
        print(f"refused: decompression bomb ({e})", file=sys.stderr)
        return EXIT_DECOMPRESSION_BOMB
    except CorruptPDFError as e:
        print(f"refused: corrupt PDF ({e})", file=sys.stderr)
        return EXIT_CORRUPT
    except MaliciousPDFError as e:
        print(f"refused: malicious PDF ({e})", file=sys.stderr)
        return EXIT_MALICIOUS
    except ContentDriftError as e:
        print(f"aborted: content drift ({e})", file=sys.stderr)
        return EXIT_VERIFIER_FAIL
    except CompressError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ENGINE_ERROR

    # Write output
    if str(args.output) == "-":
        sys.stdout.buffer.write(output_bytes)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(output_bytes)

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
