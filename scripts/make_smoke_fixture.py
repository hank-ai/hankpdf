#!/usr/bin/env python3
"""Generate a minimal blank-page PDF used by the Docker smoke test.

Writes tests/fixtures/smoke.pdf. Idempotent — overwrites on every run.
Kept intentionally tiny (single blank letter-sized page) so the Docker
image's compress path can run end-to-end in well under a second.
"""

from __future__ import annotations

import pathlib
import sys

import pikepdf


def main() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    target = root / "tests" / "fixtures" / "smoke.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))  # US letter at 72 dpi
    pdf.save(target)

    size = target.stat().st_size
    print(f"wrote {target} ({size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
