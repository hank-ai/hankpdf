# HankPDF

Aggressive, safety-first PDF shrinker for scanned documents. Takes a PDF in, produces a shrunk/resized searchable PDF out. **Local tool. No network, no telemetry, no data leaves your machine.** Targets 8–15× typical compression (up to 200× on text-dominant scans) while preserving OCR searchability and guaranteeing no silent content loss.

_Repo / package name: `pdf-smasher`. Product brand: **HankPDF**._

**Status:** Architecture and planning phase. No code yet. Docs under `docs/` are the source of truth.

## What it does

Takes oversized scanned PDFs (typical input: 200-page, 800 MB image scans) and produces compact, searchable, verified outputs. **CLI-first. Two install targets, both run the same engine locally**:

1. **Python package** — `pip install pdf-smasher`. Brings the `compress()` API and the `hankpdf` console script. Requires Tesseract + jbig2enc via your system package manager (one-line install on every major OS — see `docs/INSTALL.md`).
2. **Docker image** — `ghcr.io/ourorg/pdf-smasher:X.Y`. All native deps baked in; zero host setup. Ideal for CI/CD, SFTP upload wrappers, batch jobs, and any environment where installing Tesseract on the host is inconvenient.

**Not a service, not a GUI, not a signed installer.** HankPDF is a command-line tool. It runs entirely on the user's machine, never uploads PDFs anywhere, never phones home, writes no analytics, stores no persistent state beyond what the user asks (output PDF, optional sidecar manifest).

## What makes it different

- **Permissive license throughout** — no AGPL, no commercial SDK dependency. Built entirely on Apache-2.0 / BSD / MPL-2.0 components. We can ship, modify, and redistribute freely.
- **Content-preservation invariant** — every output is gated by OCR-text diff, tile-level SSIM, and structural audit. We refuse rather than silently corrupt.
- **Weird-PDF robust** — encrypted, signed, corrupt-xref, JBIG2-in, form XObjects, color profiles, linearized, tagged, PDF/A-3-embedded: each class has an explicit detect-and-handle policy. None crash the pipeline.
- **Honest compression targets** — we promise what we deliver: ≥3× guaranteed, 8–15× typical, 50–200× best-case on text-only content. Not 200× on everything.

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

**Per-page image export (JPEG, PNG, or WebP):**

```bash
hankpdf in.pdf -o page.jpg --pages 1 --image-dpi 150 --jpeg-quality 80
hankpdf in.pdf -o dump.png --image-dpi 200
hankpdf in.pdf -o small.webp --pages 1-5 --webp-quality 70
```

Image export skips the MRC compression pipeline; each requested page is rendered and saved as a standalone image. The output format is inferred from the `-o` extension (or set explicitly via `--output-format`). Use `--pages` to restrict to a subset — without it, every page is exported.

## Documentation

| Doc | Purpose |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Design decisions, rationale, system diagram. The *why*. |
| [docs/SPEC.md](docs/SPEC.md) | Functional spec — CLI contract, API surface, behaviors, edge-case policies. The *what*. |
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
