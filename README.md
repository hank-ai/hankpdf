# HankPDF

Aggressive, safety-first PDF shrinker for scanned documents. Takes a PDF in, produces a shrunk/resized searchable PDF out. **Local tool. No network, no telemetry, no data leaves your machine.** Targets 8–15× typical compression (up to 200× on text-dominant scans) while preserving OCR searchability and guaranteeing no silent content loss.

**Status:** v0.2.0 — 393 tests passing on Linux / macOS / Windows CI. Available via PyPI (`pip install hankpdf`), GHCR (`docker pull ghcr.io/hank-ai/hankpdf:v0.2.0`), or from the repo (see **Setup** below).

> **v0.2.0 rename note:** the PyPI distribution and import package were renamed from `pdf-smasher` to `hankpdf` so the install name matches the CLI command, GHCR image, and product brand. The legacy `pdf_smasher` import package still works for one cycle (emits a `DeprecationWarning` pointing at `hankpdf`); removal in v0.3.0. The yanked `pdf-smasher 0.1.0` on PyPI continues to install at the exact pin (PEP 592) but bare `pip install pdf-smasher` no longer resolves.

> ## ⚠️ Required: native binaries (`pip install` is NOT enough)
>
> HankPDF is a Python wrapper around three native CLI tools. **`pip install hankpdf` does NOT install them — you have to do it via your system package manager BEFORE the wrapper works**, or use the Docker image which has them baked in.
>
> | Binary | Why | Without it |
> |---|---|---|
> | **Tesseract** | OCR text-layer extraction + verifier | `hankpdf` exits with `tesseract is not installed or it's not in your PATH` on any input that needs OCR |
> | **qpdf** | structural repair + linearization | `EnvironmentError`, refuses to run |
> | **jbig2enc** | text-region encoder for the MRC pipeline | text-only compression drops from ~50× to ~6× (flate fallback); searchable PDFs still produced |
>
> Install them via your OS package manager (`brew install tesseract qpdf` on macOS, `apt install tesseract-ocr qpdf jbig2enc-tools` on Debian/Ubuntu, `choco install tesseract qpdf` on Windows). Build jbig2enc from source where it isn't packaged — see [docs/INSTALL.md](docs/INSTALL.md) for full per-OS instructions including the Windows `jbig2.exe` installer.
>
> **After install, run `hankpdf --doctor`** — it prints the version of every binary it can find and `NOT FOUND` for any that's missing. Run it before you trust any other output.
>
> **Want zero setup?** Use the Docker image (`docker pull ghcr.io/hank-ai/hankpdf:v0.1.0`) — Tesseract, qpdf, and jbig2enc are all baked in. See **Setup → Docker** below.

## What it does

Takes oversized scanned PDFs (typical input: 200-page, 800 MB image scans) and produces compact, searchable, verified outputs. **CLI-first. Two install targets, both run the same engine locally**:

1. **Python package** — `pip install hankpdf` (gives you the `compress()` API and the `hankpdf` console script) **plus** Tesseract + qpdf + jbig2enc via your system package manager. See the loud callout above and [docs/INSTALL.md](docs/INSTALL.md) for one-line per-OS install instructions.
2. **Docker image** — `ghcr.io/hank-ai/hankpdf:latest`. All native deps baked in; zero host setup. Ideal for CI/CD, SFTP upload wrappers, batch jobs, and any environment where installing Tesseract on the host is inconvenient.

**Not a service, not a GUI, not a signed installer.** HankPDF is a command-line tool. It runs entirely on the user's machine, never uploads PDFs anywhere, never phones home, writes no analytics, stores no persistent state beyond what the user asks (output PDF, optional sidecar manifest).

## What makes it different

- **Permissive license throughout** — no AGPL, no commercial SDK dependency. Built entirely on Apache-2.0 / BSD / MPL-2.0 components. We can ship, modify, and redistribute freely.
- **Content-preservation invariant** — every output is gated by OCR-text diff, tile-level SSIM, and structural audit. We refuse rather than silently corrupt.
- **Weird-PDF robust** — encrypted, signed, corrupt-xref, JBIG2-in, form XObjects, color profiles, linearized, tagged, PDF/A-3-embedded: each class has an explicit detect-and-handle policy. None crash the pipeline.
- **Honest compression targets** — for **scanned-document inputs**, we deliver ≥3× guaranteed, 8–15× typical, 50–200× best-case on text-only scans. **For PDFs that are already efficiently encoded** (vector slide decks, presentations, native exports from Word/Powerpoint), the MRC re-rasterize-and-recompress pipeline can produce *larger* output — so the default `--min-ratio 1.5` short-circuits to passthrough rather than churning, and text-only inputs now passthrough even faster via the per-page MRC gate (see below). See [docs/PERFORMANCE.md](docs/PERFORMANCE.md) for measured ratios across input types and settings.

> **Defaults preserve any existing text layer** (byte-faithful to the source, no flag required). `--ocr` means *ensure* searchable — it runs Tesseract only on pages where the input has no text or the existing text fails a quality heuristic. `--strip-text-layer` opts out (text-free output); `--re-ocr` forces Tesseract everywhere even when the input has good native text. See [docs/PERFORMANCE.md](docs/PERFORMANCE.md) for measured behavior across the four scenarios.

## Output modes

HankPDF produces three output shapes out of the same `hankpdf` command:

**PDF (default):**

```bash
hankpdf in.pdf -o out.pdf
```

**Chunked PDF (for email attachment limits):**

```bash
hankpdf in.pdf -o out.pdf --max-output-mb 25
# writes out_001.pdf, out_002.pdf, ... if the merged output exceeds 25 MB
```

Chunk filenames are zero-padded, 1-indexed, and preserve page order. A single page that's already larger than the cap is emitted alone (you'll see a stderr warning).

> **Scheme change in schema v2:** chunk filenames are now `{base}_{NNN}{ext}` (1-indexed, min 3-digit zero-pad). The previous scheme was `{base}_{idx}{ext}` (0-indexed, unpadded). Automation pinned to `out_0.pdf` should migrate to `out_001.pdf` or glob `out_*.pdf` with a lexical sort.

**Per-page image export (JPEG, PNG, or WebP):**

```bash
hankpdf in.pdf -o page.jpg --pages 1 --image-dpi 150 --jpeg-quality 80
hankpdf in.pdf -o dump.png --image-dpi 200
hankpdf in.pdf -o small.webp --pages 1-5 --webp-quality 70
```

Image export skips the MRC compression pipeline; each requested page is rendered and saved as a standalone image. The output format is inferred from the `-o` extension (or set explicitly via `--output-format`). Use `--pages` to restrict to a subset — without it, every page is exported.

## Passthrough on low compressibility

By default HankPDF returns the input **unchanged** if the achieved ratio
is below **1.5×** — producing an MRC output larger than the input serves
no one. The run exits 0 with `status="passed_through"` (exit code 2) and
a warning code `passthrough-ratio-floor` on stderr; `report.output_sha256`
equals `report.input_sha256` and the verifier is marked `"skipped"` with
fail-closed sentinels so downstream gates can't mistake passthrough for
a clean verified run.

Overrides:

```bash
hankpdf in.pdf -o out.pdf --min-ratio 1.0   # force MRC output regardless
hankpdf in.pdf -o out.pdf --min-ratio 0     # disable the floor entirely
hankpdf in.pdf -o out.pdf --min-input-mb 5  # also passthrough if input < 5 MB
```

`--min-input-mb` is a sibling gate for inputs so small that the MRC
per-page overhead (~2-3 s/page) isn't worth the ratio gain; it emits
warning code `passthrough-min-input-mb`.

A third gate runs **before** both of those: a per-page MRC-worthiness
classifier that skips the pipeline entirely on pages with no meaningful
image content. For each page, `image_xobject_bytes / page_byte_budget`
is compared against `--per-page-min-image-fraction` (default `0.30`);
pages below the threshold are emitted verbatim (no rasterize, no
compose, no verify). When no page meets the threshold the whole-doc
shortcut returns the input bytes unchanged with warning code
`passthrough-no-image-content`; partial runs emit
`pages-skipped-verbatim-N`. The gate is bypassed by `--re-ocr`,
`--strip-text-layer`, `--legal-mode`, `--verify`, or
`--per-page-min-image-fraction 0`. See [docs/PERFORMANCE.md](docs/PERFORMANCE.md)
"Per-page MRC gate" for measurements.

## Setup

### Hand this repo to Claude Code / Codex / any coding agent

If you're not a developer, the fastest path is to clone the repo, open it in an agent-capable editor (Claude Code, Cursor, Copilot, etc.), and paste this prompt:

> Read `README.md` and `docs/INSTALL.md`. Detect my operating system. Install every native dependency HankPDF needs (Python 3.14, uv, Tesseract, qpdf, jbig2enc), clone any missing binaries, run `uv sync --all-extras`, then run `uv run pytest -q` and report the result. If any step needs my input (sudo password, GitHub auth, WSL activation), stop and tell me.

The agent reads the OS-specific blocks below, runs the commands, reports test output. You'll be up in ~5-15 minutes depending on network and whether jbig2enc needs building from source.

### Docker (any OS)

Zero host setup. Works on macOS, Linux, and Windows with Docker Desktop.

```bash
docker pull ghcr.io/hank-ai/hankpdf:latest

# macOS / Windows (Docker Desktop handles uid mapping):
docker run --rm -v "$PWD:/data" ghcr.io/hank-ai/hankpdf:latest \
    /data/in.pdf -o /data/out.pdf

# Linux (pass -u so the container can write to your bind mount):
docker run --rm -u "$(id -u):$(id -g)" -v "$PWD:/data" \
    ghcr.io/hank-ai/hankpdf:latest \
    /data/in.pdf -o /data/out.pdf
```

**For production use, pin to an immutable tag or digest:**

```bash
# Immutable — a specific release:
docker pull ghcr.io/hank-ai/hankpdf:<version-tag>

# Immutable — a specific commit SHA:
docker pull ghcr.io/hank-ai/hankpdf@sha256:<digest>

# `:latest` is MUTABLE — it floats with every main-branch merge.
# Fine for local dev; never for production batch jobs where you
# want to know exactly what bytes ran.
```

Every pushed image is signed with cosign (keyless, via GitHub OIDC)
and carries a SLSA v1 build-provenance attestation. Verify before
running in production:

```bash
cosign verify ghcr.io/hank-ai/hankpdf:<version-tag> \
    --certificate-identity-regexp 'https://github\.com/hank-ai/(hankpdf|pdf-smasher)/\.github/workflows/docker\.yml@refs/(heads|tags)/.+' \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

See [docker/README.md](docker/README.md) for tag semantics, uid
rationale, and local-build instructions.

### Manual setup — macOS

```bash
# 1. Python 3.14 + uv
brew install python@3.14
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Native deps (tesseract + qpdf via brew; jbig2enc from source)
brew install tesseract qpdf
git clone --depth=1 https://github.com/agl/jbig2enc.git /tmp/jbig2enc
cd /tmp/jbig2enc && ./autogen.sh && ./configure && make && sudo make install

# 3. HankPDF
git clone git@github.com:hank-ai/hankpdf.git
cd hankpdf
uv sync --all-extras
uv run hankpdf --version       # smoke test
uv run pytest -q               # full test suite (~1 min)
```

### Manual setup — Linux (Debian / Ubuntu)

```bash
# 1. Python 3.14 (deadsnakes PPA on Ubuntu <25.04)
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.14 python3.14-venv python3.14-dev

# 2. uv
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"

# 3. Native deps
sudo apt install -y tesseract-ocr libtesseract-dev qpdf jbig2enc-tools

# 4. HankPDF
git clone git@github.com:hank-ai/hankpdf.git
cd hankpdf
uv sync --all-extras
uv run hankpdf --version
uv run pytest -q
```

(Fedora/RHEL: swap `apt install` for `dnf install -y tesseract qpdf leptonica-devel`; jbig2enc still needs building from source per `docs/INSTALL.md`.)

### Manual setup — Windows

Three paths. **Pick Docker unless you need a native Python install.**

**Option 0 — Docker Desktop (easiest for non-developers):**

```powershell
# Install Docker Desktop from docker.com
docker pull ghcr.io/hank-ai/hankpdf:latest
docker run --rm -v "${PWD}:/data" ghcr.io/hank-ai/hankpdf:latest `
    /data/in.pdf -o /data/out.pdf
```

No Python, no native deps, no WSL needed. Works on Windows 10/11
Pro/Enterprise/Home with WSL2 backend enabled. Docker Desktop licensing
is free for personal use, education, and small businesses (<= 250 employees
and < $10M annual revenue). Larger orgs need a Docker Business license or
can install Docker Engine via WSL instead.

**Option A — WSL2 (recommended):** run the Linux instructions above inside WSL Ubuntu. From PowerShell (admin):

```powershell
wsl --install -d Ubuntu-24.04
```

Reboot, then follow the Linux block inside the Ubuntu shell. Your Windows files are at `/mnt/c/Users/<YourUser>/...`.

**Option B — Native Windows (PowerShell):**

```powershell
winget install Python.Python.3.14
choco install tesseract qpdf -y
irm https://astral.sh/uv/install.ps1 | iex

# Install jbig2.exe for full MRC compression (optional; CCITT G4 fallback works without it).
# Tagged URL — main is a mutable branch, so we pin to a specific release.
# Replace the tag with whatever "jbig2-windows-v*" tag you want to install.
$tag = "jbig2-windows-v0.1.0"
irm "https://github.com/hank-ai/hankpdf/releases/download/$tag/install_jbig2_windows.ps1" | iex

git clone git@github.com:hank-ai/hankpdf.git
cd hankpdf
uv sync --all-extras
uv run hankpdf --version
uv run pytest -q
```

The jbig2 installer pulls a prebuilt `jbig2.exe` (plus its runtime
DLLs) from the hankpdf GitHub Releases, extracts it to
`%LOCALAPPDATA%\hankpdf\bin`, and registers that directory on your
user PATH. No administrator required. Open a new terminal after the
installer runs so the PATH update is picked up. Source and build
recipe live in `scripts/install_jbig2_windows.ps1` and
`.github/workflows/windows-jbig2enc.yml`.

Without the jbig2 installer, the MRC pipeline falls back to CCITT G4
for the text layer — outputs are typically 10-20% larger than with
jbig2enc, but every other feature works identically and all tests pass.

### Put `hankpdf` on your PATH

After `uv sync` succeeds, you can either keep prefixing with `uv run hankpdf`, or install the console script system-wide:

```bash
uv tool install --from . hankpdf
hankpdf --version
```

### Running tests

```bash
uv run pytest -q                          # all 393 tests (~1 min)
uv run pytest tests/unit -v               # unit only (~10 s)
uv run pytest -m integration -v           # integration only
uv run pytest --cov=hankpdf           # with coverage
uv run ruff check hankpdf tests       # lint
uv run mypy hankpdf                   # type check
```

### Troubleshooting

- `hankpdf --version` — prints Python version, hankpdf version, and every native dep's version + path. If one is missing, that's your install problem.
- `uv run python -c "import hankpdf; print('OK')"` — import smoke test.
- OCR unit tests auto-skip when `tesseract` isn't on PATH. The rest of the suite should pass regardless.
- Still stuck? Open an issue with `hankpdf --version` output attached.

## Documentation

| Doc | Purpose |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Design decisions, rationale, system diagram. The *why*. |
| [docs/SPEC.md](docs/SPEC.md) | Functional spec — CLI contract, API surface, behaviors, edge-case policies. The *what*. |
| [docs/PERFORMANCE.md](docs/PERFORMANCE.md) | Measured compression ratios + wall-time across input types and settings. The *how-fast and how-small*. |
| [docs/KNOWLEDGE.md](docs/KNOWLEDGE.md) | Reference material: MRC algorithm, codec trade-offs, license notes, PDF internals, prior-art summaries. The *background*. |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Phased implementation checklist. The *how and when*. |

## License

HankPDF is licensed under **Apache-2.0** ([LICENSE](LICENSE)).

### Commercial use

HankPDF is **cleared for commercial use**. The dependency tree was chosen
specifically to avoid the commercial-licensing blockers common in PDF/OCR
tooling (Ghostscript AGPL, Poppler GPL, ABBYY/Nuance per-seat licensing).

See [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) for the full
dependency audit — every Python package, native binary, and transitive
system library with its SPDX identifier and commercial-use status. That
file is the canonical reference for any licensing question; re-audit
before every release.

Attribution for bundled/runtime third-party code is in [NOTICE](NOTICE).
