#!/usr/bin/env python3
"""Measure HankPDF compression ratios on a directory of PDFs.

Usage:
    uv run python scripts/measure_ratios.py <input-dir> [--force-monochrome]

Emits a markdown table to stdout. Exits 0 on success (even if some files
are refused by CompressError — those are valid outcomes). Exits 1 if any
UNEXPECTED exception was raised, with a banner in the output.

Filenames are shown as ``<sha1(basename)[:8]>…<basename[-8:]>`` per SPEC
§9.2 log-redaction policy. Unicode filenames (NFC/NFD, emoji) are
normalized to NFC before display (SPEC §4 unicode-filename row).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import unicodedata
from pathlib import Path

import pikepdf

from pdf_smasher import CompressError, CompressOptions, compress

_NAME_TAIL_LEN = 8


def _redact(name: str) -> str:
    normed = unicodedata.normalize("NFC", name)
    digest = hashlib.sha1(normed.encode("utf-8")).hexdigest()
    tail = normed[-_NAME_TAIL_LEN:] if len(normed) > _NAME_TAIL_LEN else normed
    return f"{digest[:_NAME_TAIL_LEN]}…{tail}"


def run(input_dir: Path, *, force_monochrome: bool) -> int:
    pdfs = sorted(p for p in input_dir.glob("*.pdf") if p.is_file())
    if not pdfs:
        print(f"no PDFs found in {input_dir}", file=sys.stderr)
        return 2

    opts = CompressOptions(force_monochrome=force_monochrome)
    print(
        "| File | Input (bytes) | Output (bytes) | Ratio | Pages | Wall (ms) | Verifier |",
    )
    print("|---|---:|---:|---:|---:|---:|---|")

    total_in = 0
    total_out = 0
    n_ok = 0
    n_refused = 0
    n_crashed = 0

    for p in pdfs:
        data = p.read_bytes()
        label = _redact(p.name)
        try:
            _, report = compress(data, options=opts)
        except CompressError as e:
            n_refused += 1
            print(f"| {label} | {len(data):,} | — | — | — | — | REFUSED: {type(e).__name__} |")
            continue
        except pikepdf.PdfError as e:
            n_refused += 1
            print(f"| {label} | {len(data):,} | — | — | — | — | CORRUPT: {e} |")
            continue
        except Exception as e:
            n_crashed += 1
            print(
                f"| {label} | {len(data):,} | — | — | — | — | "
                f"*** CRASH: {type(e).__name__}: {e} *** |",
            )
            continue
        n_ok += 1
        total_in += report.input_bytes
        total_out += report.output_bytes
        print(
            f"| {label} | {report.input_bytes:,} | {report.output_bytes:,} | "
            f"{report.ratio:.2f}x | {report.pages} | {report.wall_time_ms} | "
            f"{report.verifier.status} |",
        )

    print()
    if n_crashed > 0:
        print(f"**⚠ {n_crashed} CRASHES** — these are bugs, not refusals. Ratio totals omitted.")
        return 1

    if total_in > 0:
        print(
            f"| **TOTAL ({n_ok} ok, {n_refused} refused)** | "
            f"**{total_in:,}** | **{total_out:,}** | "
            f"**{total_in / max(1, total_out):.2f}x** | — | — | — |",
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("--force-monochrome", action="store_true")
    args = parser.parse_args()
    return run(args.input_dir, force_monochrome=args.force_monochrome)


if __name__ == "__main__":
    raise SystemExit(main())
