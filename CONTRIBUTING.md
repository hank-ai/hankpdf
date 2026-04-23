# Contributing to HankPDF

HankPDF (package name `pdf-smasher`) is Apache-2.0 licensed. Contributions are welcome once the repository is public — this file is kept current so it's ready on day one.

## Dev setup

Prerequisites: Python 3.14 (standard GIL build), and the native deps listed in `docs/INSTALL.md` (Tesseract + jbig2enc + qpdf on the system PATH).

We use [`uv`](https://github.com/astral-sh/uv) as the package manager.

```bash
# One-time
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install project + dev deps into a local venv
uv sync --all-extras --dev
```

## Running tests

```bash
uv run pytest                          # unit + integration (fast)
uv run pytest -m slow                  # slow / corpus-backed tests
uv run pytest -m 'integration'         # integration only
uv run pytest --cov=pdf_smasher        # with coverage
```

## Linting and type checks

```bash
uv run ruff check pdf_smasher tests    # lint
uv run ruff format pdf_smasher tests   # format
uv run mypy pdf_smasher                # strict type check
```

Pre-commit hooks run a subset of the above on every commit:

```bash
uv run pre-commit install               # one-time
uv run pre-commit run --all-files       # manual run
```

## PR conventions

- Branch off `main`; keep PRs small and focused.
- Every PR gets CI green (lint, type-check, unit tests, integration tests) before merge.
- Commits use Conventional-Commits-ish format. Accepted type prefixes:
  - `feat:` — new user-visible capability
  - `fix:` — bug fix
  - `chore:` — tooling / deps / housekeeping
  - `docs:` — documentation-only change
  - `refactor:` — internal restructure with no behavior change
  - `test:` — tests-only change
  - `perf:` — measurable performance improvement
  - `security:` — security-hardening change that isn't purely a bugfix
    (e.g., new gate, tightened default)
  - `observability:` — logging / metrics / progress-event surface changes
  - `diag:` — diagnostic tooling / scripts not shipped as product
  A scope in parentheses is encouraged for multi-module repos, e.g.
  `fix(cli): …` or `feat(image-export): …`.
- Tests are required for new functionality. Golden-output tests record expected ratio bands, not exact sizes — cross-host non-determinism is real (see `docs/ARCHITECTURE.md` §5 on the Tesseract LSTM non-determinism gotcha).
- No new AGPL / GPL dependencies. Permissive licenses only (Apache-2.0 / BSD / MIT / MPL-2.0).

## Corpus

Real PDFs are not committed. They live in an S3 bucket and are fetched on demand by `scripts/fetch_corpus.py` against `tests/corpus/manifest.json`. See `tests/corpus/README.md` for how to add a fixture.

## Design docs

All design decisions are captured in `docs/`:

- `ARCHITECTURE.md` — the *why*
- `SPEC.md` — the *what*
- `KNOWLEDGE.md` — background material
- `ROADMAP.md` — phased checklist

Before changing behavior, read the relevant section. If your change contradicts something there, update the doc in the same PR.
