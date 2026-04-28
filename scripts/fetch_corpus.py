#!/usr/bin/env python3
"""Fetch the test corpus described in tests/corpus/manifest.json.

Downloads each fixture to tests/corpus/_cache/ and verifies SHA-256.
Idempotent: skips files that are already present and hash-valid.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "tests" / "corpus" / "manifest.json"
CACHE_DIR = ROOT / "tests" / "corpus" / "_cache"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_one(fixture: dict, *, force: bool = False) -> bool:
    filename = fixture["filename"]
    expected_sha = fixture["sha256"]
    # Prefer the mirror_url when present (anything urllib can fetch — S3,
    # HTTPS, file:// — your choice); fall back to upstream URL.
    source = fixture.get("mirror_url") or fixture["url"]

    target = CACHE_DIR / filename
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if target.exists() and not force:
        actual = sha256_of(target)
        if actual == expected_sha:
            print(f"ok       {filename} (cached)")
            return True
        print(f"stale    {filename} — sha mismatch, re-fetching")

    print(f"fetching {filename} from {source}")
    try:
        with urllib.request.urlopen(source) as response, target.open("wb") as out:
            while chunk := response.read(1024 * 1024):
                out.write(chunk)
    except Exception as e:
        print(f"error    {filename}: {e}", file=sys.stderr)
        return False

    actual = sha256_of(target)
    if actual != expected_sha:
        print(
            f"fail     {filename}: sha256 mismatch "
            f"(expected {expected_sha[:12]}…, got {actual[:12]}…)",
            file=sys.stderr,
        )
        target.unlink(missing_ok=True)
        return False

    print(f"ok       {filename} ({target.stat().st_size:,} bytes)")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="fetch only fixtures with this tag")
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-fetch even if a cached file hashes OK",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="only verify existing cached files; don't fetch",
    )
    args = parser.parse_args()

    if not MANIFEST_PATH.exists():
        print(f"manifest not found: {MANIFEST_PATH}", file=sys.stderr)
        return 2

    manifest = json.loads(MANIFEST_PATH.read_text())
    fixtures = manifest.get("fixtures", [])

    if args.tag:
        fixtures = [f for f in fixtures if args.tag in f.get("tags", [])]

    if not fixtures:
        print("no fixtures in manifest (or none match the tag filter).")
        return 0

    if args.verify:
        all_ok = True
        for f in fixtures:
            target = CACHE_DIR / f["filename"]
            if not target.exists():
                print(f"missing  {f['filename']}")
                all_ok = False
                continue
            actual = sha256_of(target)
            if actual == f["sha256"]:
                print(f"ok       {f['filename']}")
            else:
                print(f"fail     {f['filename']}: sha mismatch")
                all_ok = False
        return 0 if all_ok else 1

    any_failed = False
    for f in fixtures:
        if not fetch_one(f, force=args.force):
            any_failed = True

    return 1 if any_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
