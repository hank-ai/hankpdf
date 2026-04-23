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

    # OCR — off by default. --ocr embeds a searchable text layer (adds ~5s/pg).
    p.add_argument("--ocr", dest="ocr", action="store_true", default=False)
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
    p.add_argument(
        "--max-output-mb",
        type=float,
        default=None,
        help=(
            "Cap the output PDF size. If the compressed output exceeds this "
            "value, split into multiple files named {base}_0{ext}, "
            "{base}_1{ext}, ... preserving page order. Useful for email "
            "attachment limits, archival chunk sizes, etc. A single page "
            "that's already larger than the cap is emitted alone (you'll "
            "see a warning)."
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
        type=int,
        default=0,
        help=(
            "Per-page parallelism. 0 (default) = auto (cpu_count-2, min 1). "
            "1 = serial. N>1 = exactly N workers. Each worker gets its own "
            "single-page PDF slice, never the whole source."
        ),
    )

    # Reporting
    p.add_argument("--report", choices=["text", "json", "jsonl", "none"], default="text")
    p.add_argument("--quiet", action="store_true")
    return p


def _parse_pages_spec(spec: str) -> set[int]:
    """Parse a 1-indexed page spec like '1,3-5,10' into a set of ints.

    Raises ValueError on malformed input.
    """
    out: set[int] = set()
    for raw in spec.split(","):
        part = raw.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, hi_s = part.split("-", 1)
            lo, hi = int(lo_s), int(hi_s)
            if lo < 1 or hi < lo:
                msg = f"invalid range {part!r}: must be 1-indexed, lo <= hi"
                raise ValueError(msg)
            out.update(range(lo, hi + 1))
        else:
            n = int(part)
            if n < 1:
                msg = f"invalid page {part!r}: must be 1-indexed"
                raise ValueError(msg)
            out.add(n)
    return out


def _read_password(args: argparse.Namespace) -> str | None:
    if args.password_file is not None:
        return args.password_file.read_text().strip() or None
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

    only_pages: set[int] | None = None
    if args.pages:
        try:
            only_pages = _parse_pages_spec(args.pages)
        except ValueError as e:
            print(f"error: --pages {e}", file=sys.stderr)
            return EXIT_USAGE

    # Progress: tqdm bar for the per-page phase + plain stderr lines for
    # the triage/merge/verify milestones. All output goes to stderr so that
    # --report json on stdout stays clean for piping. --quiet suppresses both.
    from tqdm import tqdm  # type: ignore[import-untyped]

    from pdf_smasher import ProgressEvent

    _bar: tqdm | None = None

    def _progress(event: ProgressEvent) -> None:
        nonlocal _bar
        if args.quiet:
            return
        if event.phase == "triage_complete":
            print(f"[hankpdf] {event.message}", file=sys.stderr, flush=True)
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
            tag = "pass" if event.verifier_passed else "FAIL"
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
            print(f"[hankpdf] {event.message}", file=sys.stderr, flush=True)
        elif event.phase in {"merge_complete", "triage"}:
            print(f"[hankpdf] {event.message}", file=sys.stderr, flush=True)

    try:
        try:
            output_bytes, report = compress(
                input_bytes,
                options=options,
                progress_callback=_progress,
                only_pages=only_pages,
            )
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
    finally:
        if _bar is not None:
            _bar.close()

    # Write output — possibly as multiple chunked files if --max-output-mb set.
    if str(args.output) == "-":
        # Stdout: can't split; always write the merged bytes.
        if args.max_output_mb is not None and len(output_bytes) > args.max_output_mb * 1024 * 1024:
            print(
                "warning: --max-output-mb is ignored when -o - (stdout); "
                "wrote merged output",
                file=sys.stderr,
            )
        sys.stdout.buffer.write(output_bytes)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.max_output_mb is None:
            args.output.write_bytes(output_bytes)
        else:
            from pdf_smasher.chunking import split_pdf_by_size

            max_bytes = int(args.max_output_mb * 1024 * 1024)
            chunks = split_pdf_by_size(output_bytes, max_bytes=max_bytes)
            if len(chunks) == 1:
                args.output.write_bytes(chunks[0])
            else:
                base = args.output.stem
                ext = args.output.suffix
                parent = args.output.parent
                written_paths: list[Path] = []
                for idx, chunk in enumerate(chunks):
                    p = parent / f"{base}_{idx}{ext}"
                    p.write_bytes(chunk)
                    written_paths.append(p)
                oversize = [p for p in written_paths if p.stat().st_size > max_bytes]
                if not args.quiet:
                    print(
                        f"[hankpdf] wrote {len(chunks)} chunks "
                        f"({args.max_output_mb:.1f} MB cap); "
                        f"sizes: {[f'{p.stat().st_size / (1024 * 1024):.2f} MB' for p in written_paths]}",
                        file=sys.stderr,
                    )
                    if oversize:
                        print(
                            f"[hankpdf] warning: {len(oversize)} chunk(s) exceed "
                            "the cap because they contain a single oversize page: "
                            f"{[p.name for p in oversize]}",
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
