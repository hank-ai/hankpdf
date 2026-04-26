# Test Strategy

## Tiers

**Unit** (`tests/unit/`)
- Pure-Python, no native binaries required.
- Every PR runs these on every supported platform (Linux, macOS, Windows) in CI.
- Should run in <30 seconds.
- Import tests, dataclass construction, exception hierarchy, pure helpers.

**Integration** (`tests/integration/`)
- Require native deps on PATH (Tesseract, qpdf, jbig2enc).
- Mark with `@pytest.mark.integration`.
- CI runs these on Linux and macOS (Windows only if the dep install is reliable).
- Tests the public API against real PDFs.

**Corpus** (`tests/integration/test_corpus_*.py` marked `@pytest.mark.corpus`)
- Requires fetched public-domain PDFs (see `tests/corpus/README.md`).
- Not a hard CI gate; may be skipped if the corpus isn't fetched.
- Run regularly to catch fidelity regressions.
- Cross-host: record expected ratio bands, not exact sizes.

**Slow** (`@pytest.mark.slow`)
- Heavy tests that are OK to run locally but shouldn't gate every PR.

## Markers

Declared in `pyproject.toml` `[tool.pytest.ini_options]`:

- `slow` — long-running tests
- `integration` — needs native deps
- `corpus` — needs fetched corpus

Run subsets:

```bash
pytest -m "not slow"          # default CI
pytest -m integration         # integration only
pytest -m corpus              # corpus-backed
pytest --no-header -rN        # quiet
```

## Determinism contract

See `docs/SPEC.md` §12.

- **Tesseract LSTM is NOT deterministic across hosts** (float32 BLAS ordering varies). Text-equality tests run only on a pinned CI image; cross-host tests use ratio bands + SSIM tolerance.
- **Our own code** (triage, mask generation given fixed input, idempotency canonicalization, verifier thresholds) IS deterministic within a host.

## Golden-output tests

Store expected output properties, NOT byte-exact expected bytes. A golden test asserts:

- Ratio lands in a band (e.g. "between 4 and 8 MB for this input").
- Verifier status (pass / fail / skipped).
- Exit code.
- Strip list.
- Structural audit (page count, annot count).

Store golden expectations in `tests/corpus/golden/<fixture>.json`. Don't store golden PDF outputs — they'd drift across pdfium revisions.

## How to add a corpus fixture

1. Find a public-domain PDF. Ideal sources:
   - Internet Archive (non-AGPL material)
   - govinfo.gov
   - USPTO patent scans
   - Public-domain book scans (Project Gutenberg alternatives)
2. Download and compute SHA-256.
3. Upload to a mirror you control. Anything `urllib` can fetch works — S3 via HTTPS, your own HTTPS host, even a `file://` path during local development. The mirror is optional; the upstream `url` is the fallback.
4. Add entry to `tests/corpus/manifest.json`:
   ```json
   {
     "filename": "govinfo-2021-house-hearing.pdf",
     "url": "https://www.govinfo.gov/...",
     "mirror_url": "https://example.com/mirror/govinfo-2021-house-hearing.pdf",
     "sha256": "abc123...",
     "tags": ["mono", "text", "large", "linearized"],
     "license": "Public domain (U.S. government work)",
     "notes": "200+ page hearing transcript; good monochrome baseline"
   }
   ```
5. Run `scripts/fetch_corpus.py` locally to pull to `tests/corpus/_cache/`.
6. Write a corpus-tier test that references the fixture by filename.

## CI matrix

| Job | OS | Python | Tier |
|---|---|---|---|
| lint | Ubuntu | 3.14 | ruff + mypy only |
| test | Ubuntu, macOS, Windows | 3.14 | unit |
| integration (future) | Ubuntu, macOS | 3.14 | unit + integration |
| corpus (nightly) | Ubuntu | 3.14 | corpus + slow |
