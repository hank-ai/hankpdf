# Synthetic Weird-PDF Generators

Each script in this directory produces a small, deterministic fixture for one weird-PDF class in the taxonomy (`docs/SPEC.md` §4).

Outputs land in `tests/corpus/_generated/` (gitignored).

## Classes covered (Phase 2 builds these out)

- `make_corrupt_xref.py` — valid PDF with a xref table whose offsets are wrong but recoverable via qpdf scan
- `make_jbig2_in_stream.py` — PDF that uses `/JBIG2Decode` filter on an image — we must pass-through, not re-decode
- `make_malformed_length.py` — stream whose `/Length` disagrees with actual byte count
- `make_huge_page.py` — page with `/MediaBox` > 200"×200"
- `make_decompression_bomb.py` — page that would decompress to > `MAX_IMAGE_PIXELS`
- `make_recursive_forms.py` — Form XObject that references itself
- `make_signed.py` — PDF with a simple PKCS#7 digital signature for invalidation-policy tests

Phase 0 intentionally leaves these as empty stubs; implementation is Phase 2 T2.1 work.

## Reproducibility

Every generator is pure-Python, takes no arguments beyond an output path, and must produce byte-identical output on repeated runs (no timestamps, no UUIDs). Phase 2 CI will assert this.
