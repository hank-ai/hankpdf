# Test Corpus

Real PDFs are **not committed**. They're fetched on demand from the URLs (or S3 mirror) listed in `manifest.json`, into `_cache/` which is `.gitignore`'d.

## Add a fixture

See `tests/STRATEGY.md` → "How to add a corpus fixture."

## Fetch

```bash
python scripts/fetch_corpus.py         # fetch everything
python scripts/fetch_corpus.py --tag mono    # fetch only fixtures tagged "mono"
python scripts/fetch_corpus.py --verify      # fetch + SHA-256 verify
```

## Synthetic fixtures

`scripts/generate_corpus/` builds deterministic synthetic weird-PDF fixtures (corrupt xref, JBIG2-in-stream, malformed `/Length`, etc.). Those land in `_generated/`, also `.gitignore`'d.

## Layout

```
tests/corpus/
├── README.md           (this file)
├── manifest.json       (upstream URLs + SHA-256 + tags + license)
├── _cache/             (downloaded real PDFs; gitignored)
├── _generated/         (synthetic fixtures; gitignored)
└── golden/             (expected-output JSON per fixture; committed)
```
