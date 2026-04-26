# Performance & quality reference

Measured benchmarks across representative inputs and the full settings matrix. Numbers in this doc come from a single run on an Apple Silicon laptop (M-series), Python 3.14, hankpdf at commit `a1bef27`. Re-run via `python /tmp/hankpdf-bench/run_bench.py` (or adapt the script to your inputs).

## TL;DR

- **Use HankPDF when the input is a scanned document.** Compression of 2.7×–5.5× is typical on real-world scan-derived slide decks; higher on true scanned text.
- **Don't use HankPDF on already-efficient PDFs.** Native-export presentations (PowerPoint → PDF, Word → PDF) inflate when run through the MRC pipeline because the embedded JPEGs are already at high compression. The default `--min-ratio 1.5` correctly passthrough's these inputs unchanged.
- **Pass `--ocr` if you want a searchable output.** The MRC pipeline rasterizes every page; without `--ocr`, the output has zero text — even if the input had a text layer. This bites scan-with-existing-OCR inputs.
- **For known-scan inputs, `--mode fast` is the sweet spot.** It gets ~the same compression as `--mode standard` in roughly one-third the wall time, with no visible quality difference at letter-page DPI.
- **Image export beats the PDF pipeline on size for some workflows.** WebP at 150 DPI / quality 80 is the smallest output in our matrix and visually clean.
- **`--max-workers 0` (auto, default) is correct.** Serial mode is ~4× slower on this hardware; past `cpu_count` of perf-cores there's no further gain.
- **`--verify` is a strict, slow quality gate.** ~5.8× wall-time cost; will refuse common MRC outputs unless paired with `--ocr` and `--mode safe`. Use only when downstream consumers treat absence-of-drift as a contract.

## Test inputs

Three real-world PDFs from a 2026 conference presentation set:

| ID | Filename (truncated) | Size | Pages | Content shape |
|---|---|---|---|---|
| **I_small** | "Two Sides, One Record …" | 2.5 MB | 18 | Mixed (slides + photo) |
| **I_med** | "Boot Camp Joint Slides 2026" | 11 MB | 190 | Native-export slides (heavy text + vector) |
| **I_large** | "Scaling Anesthesia Billing and Compliance" | 23 MB | 30 | Scan-derived slides (rasterized) |

Plus one synthetic baseline:

| ID | Filename | Size | Pages | Content shape |
|---|---|---|---|---|
| **I_synth** | `tests/fixtures/smoke_text.pdf` | 175 KB | 2 | Synthetic text-only "scan" at 300 DPI |

## Settings tested

PDF compress (10 settings):

- `default` — `--min-ratio 0` (forced through pipeline; no passthrough)
- `fast` — `--mode fast --min-ratio 0`
- `safe` — `--mode safe --min-ratio 0`
- `aggressive` — `--target-bg-dpi 100 --target-color-quality 40 --min-ratio 0`
- `quality` — `--target-bg-dpi 200 --target-color-quality 75 --min-ratio 0`
- `jp2k` — `--bg-codec jpeg2000 --min-ratio 0`

Image export (4 settings):

- `image_jpg_150` — `--output-format jpeg --image-dpi 150 --jpeg-quality 75`
- `image_jpg_300` — `--output-format jpeg --image-dpi 300 --jpeg-quality 90`
- `image_png_150` — `--output-format png --image-dpi 150`
- `image_webp_150` — `--output-format webp --image-dpi 150 --webp-quality 80`

## Results

Format: `output_bytes (compression_ratio×, wall_seconds)`. Compression ratio is `input_bytes / output_bytes`; values >1 are smaller-than-input, <1 are larger.

### I_small — 2.5 MB native PDF, 18 pages

| Setting | Output | Ratio | Time | Notes |
|---|---:|---:|---:|---|
| default | 2.5 MB | 1.03× | 4.1s | Pipeline output ≈ source |
| fast | 2.2 MB | 1.16× | 2.3s | |
| safe | 2.5 MB | 1.03× | 4.2s | identical to default |
| aggressive | 1.6 MB | **1.59×** | 4.0s | ★ best PDF compress |
| quality | 4.3 MB | 0.60× | 4.3s | larger than source |
| jp2k | 2.1 MB | 1.21× | 5.7s | |
| image_jpg_150 | 3.8 MB | 0.68× | 1.7s | |
| image_jpg_300 | 15.0 MB | 0.17× | 4.6s | huge |
| image_png_150 | 10.1 MB | 0.25× | 2.5s | lossless penalty |
| image_webp_150 | 1.6 MB | **1.63×** | 3.8s | ★ best size |

Take: native-export PDF, mostly text + vector. The MRC pipeline barely beats source. Default `--min-ratio 1.5` would correctly passthrough this input unchanged.

### I_med — 11 MB native PDF, 190 pages

| Setting | Output | Ratio | Time | Notes |
|---|---:|---:|---:|---|
| default | 27.5 MB | 0.41× | 27.6s | **inflated 2.5×** |
| fast | 25.4 MB | 0.45× | 13.6s | |
| safe | 27.5 MB | 0.41× | 28.2s | identical to default |
| aggressive | 18.3 MB | 0.62× | 28.4s | least bad PDF |
| quality | 45.9 MB | 0.25× | 31.6s | inflated 4× |
| jp2k | 22.6 MB | 0.50× | 49.7s | |
| image_jpg_150 | 41.9 MB | 0.27× | 10.9s | |
| image_jpg_300 | 166.8 MB | 0.07× | 31.7s | enormous |
| image_png_150 | 106.9 MB | 0.11× | 18.7s | |
| image_webp_150 | 19.1 MB | 0.59× | 33.1s | least-bad image |

Take: 190-page slide deck of native-export slides. **Every setting inflates this input.** The MRC pipeline can't beat what's already there. Default `--min-ratio 1.5` would correctly passthrough.

### I_large — 23 MB scan-derived PDF, 30 pages ★

| Setting | Output | Ratio | Time | Notes |
|---|---:|---:|---:|---|
| default | 8.5 MB | 2.76× | 28.0s | text crisp, photo gradients soft but acceptable |
| fast | 7.8 MB | 3.03× | 10.2s | ★ same visual as default in **1/3 the time** |
| safe | 8.5 MB | 2.76× | 28.0s | identical to default |
| aggressive | **4.9 MB** | **4.75×** | 26.8s | text crisp, gradients more compressed |
| quality | 16.0 MB | 1.47× | 26.2s | near-source |
| jp2k | 7.0 MB | 3.35× | 31.9s | comparable visual to default |
| image_jpg_150 | 11.9 MB | 1.97× | 2.7s | ★ fastest path overall |
| image_jpg_300 | 56.5 MB | 0.42× | 10.4s | inflated |
| image_png_150 | 40.0 MB | 0.59× | 6.4s | inflated |
| image_webp_150 | **4.3 MB** | **5.48×** | 10.0s | ★ smallest output overall |

Take: scan-derived slide deck — exactly the input HankPDF is built for. All `--mode` settings deliver 2.7×–5.5× compression. Image export to WebP wins on raw size and is faster than the PDF pipeline.

### I_synth — 175 KB synthetic-text scan, 2 pages

| Setting | Output | Ratio | Notes |
|---|---:|---:|---|
| default | 89 KB | 2.0× | |
| fast | 89 KB | 2.0× | |
| aggressive | 79 KB | 2.3× | |

Take: this fixture is pre-compressed at source (87 KB/page is already efficient). **The README's "50-200× on text-only scans" applies to genuinely uncompressed scanner output**, not this synthetic baseline.

## Visual quality assessment

I rendered page 1 of each I_large output and the source at 100 DPI, then visually inspected them. Findings:

- **default vs source** — text is sharp and identical to the eye. The decorative gradient on the left swoosh is slightly softer in the compressed output but the difference requires close comparison.
- **fast vs default** — visually indistinguishable.
- **aggressive vs source** — text is still crisp and legible. The photo-style gradient on the swoosh shows mild banding under close inspection but is acceptable for any non-archival use.
- **quality vs source** — near-indistinguishable. Worth the size penalty only when archival fidelity is non-negotiable.
- **jp2k vs default** — comparable visual. Not a noticeable improvement at letter-page DPI.

For the synthetic-text scan (`I_synth`), `aggressive` produced text identical to source at 100 DPI render — text legibility is preserved even at the most compressed setting.

## OCR / text-findability

The MRC pipeline rasterizes every page and re-encodes it as image content. **Without `--ocr`, the output PDF has no text layer at all** — even if the input had one. This matters: a scanned PDF that arrived with an upstream text layer comes out searchable=False unless you explicitly pass `--ocr`.

Measured on a freshly-extracted-text comparison (output PDF rendered through pypdfium2 → text page count):

| Run | Input text layer | `--ocr` | Output text (chars, page 1) | Output text (total, all pages) | Wall vs no-OCR |
|---|---|---|---:|---:|---:|
| `smoke_text` (synthetic raster) | 0 chars | — | 0 chars | 0 | baseline 1.4s |
| `smoke_text` | 0 chars | yes | ~110 chars | **4,700** | +110% (2.9s) |
| `I_large` source has 194 chars (page 1) | 194 / 30 pages | — | **0 chars** | **0** | baseline 9.7s |
| `I_large` | 194 chars | yes | 181 chars | **12,769** | +34% (13.0s) |

Take-aways:

- **Pass `--ocr` if you want the output to be searchable** — even if your input is already searchable. The MRC pipeline does not preserve upstream text layers.
- OCR cost is roughly **+30-100% wall time**. On the 30-page I_large input, `--mode fast --ocr` runs 13.0s vs 9.7s without (`+34%`). On the 2-page synthetic fixture, OCR overhead is proportionally larger because compression is fast and OCR is a fixed cost per page.
- **OCR text is imperfect** — Tesseract collapses spaces in mixed regions ("HankPDFsmokefixture" was the source's "HankPDF smoke fixture"). Good enough for grep/search; not byte-exact.
- The verifier's content-drift check (next section) compares OCR text input-vs-output, so without `--ocr` on a pre-OCR'd input, the verifier will see "had text → has no text" as drift.

## Threading: `--max-workers`

Same input, settings, and machine — only the worker count varies. Input: I_large (30 pages, 23 MB scan-derived) at `--mode fast --min-ratio 0`.

| `--max-workers` | Wall time | Speedup vs serial | Notes |
|---:|---:|---:|---|
| 1 (serial) | 41.0s | 1.00× | baseline; `ProcessPoolExecutor` not invoked |
| 2 | 21.0s | 1.95× | nearly linear |
| 4 | 12.9s | 3.17× | scaling efficiency drops slightly |
| 8 | 9.7s | 4.22× | knee of the curve on this 8-perf-core M-series Mac |
| 0 (auto) | 9.7s | 4.22× | `auto` = `cpu_count - 2`; matches 8 here |

Take-aways:

- **`--max-workers 0` (auto, the default) is the right choice for most users.**
- Serial mode (`--max-workers 1`) is ~4× slower on this 30-page job. Use it only when you need single-process determinism for a specific debugging scenario.
- Diminishing returns past ~`cpu_count`-of-perf-cores; on Apple Silicon the boundary is the count of P-cores. Past 8, no measurable gain.
- Linear-ish scaling 1→4 means the per-page workload is well-isolated; the coordinator + merge stage doesn't bottleneck below ~4 workers.

## Content-drift verifier (`--verify`)

Off by default since v0.0.x. When on, the verifier re-rasterizes the output, re-runs OCR + tile-SSIM + structural checks against the input, and refuses with `EXIT_VERIFIER_FAIL=20` if drift exceeds gates. Test: I_large at default (with `--no-ocr` to isolate verifier behavior from OCR).

| Run | Wall time | Exit code | Verifier verdict |
|---|---:|---:|---|
| no `--verify` | 24.7s | 0 | (skipped) |
| `--verify` | 143.1s | **20** | `E-VERIFIER-FAIL`: content drift on 28 of 30 pages, OCR Levenshtein 0.93 vs 0.05 ceiling, SSIM tile-min -0.58 |

Take-aways:

- **Verifier wall-time cost is large** — ~5.8× the no-verify path on this input. It re-OCRs both source and output and computes tile SSIM per page.
- **Verifier is opinionated.** With `--no-ocr`, the input had a text layer that gets stripped; the verifier's OCR-text edit-distance check then sees the input's existing text vs the output's freshly-OCR'd text (now 0) — large drift. This is real signal: passthrough-without-text-layer is content drift by the verifier's definition.
- **For verifier-passing output:** combine `--verify` with `--ocr` (preserve searchability) and prefer `--mode safe` (less aggressive bg compression). On photo-heavy slide decks the SSIM gates may still flag legitimate JPEG re-encoding artifacts; use `--accept-drift` to write the output with a warning rather than refuse.
- **Don't enable `--verify` on every job.** It's a quality gate for cases where a downstream consumer treats absence-of-drift as a contract (clinical, legal archival). For typical use, the SHA + structural checks at default settings are sufficient.

## Recommendations

| Use case | Settings |
|---|---|
| Default ("I want it smaller, don't surprise me") | `hankpdf in.pdf -o out.pdf` (passthrough below 1.5×) |
| Known-scan input, want speed + searchable text | `hankpdf in.pdf -o out.pdf --mode fast --ocr` |
| Known-scan input, want maximum compression + searchable | `hankpdf in.pdf -o out.pdf --target-bg-dpi 100 --target-color-quality 40 --ocr --min-ratio 0` |
| Archival quality with verifier-passing output | `hankpdf in.pdf -o out.pdf --mode safe --ocr --verify` (slow but contractual) |
| Searchability without compression goals | `hankpdf in.pdf -o out.pdf --ocr --accept-drift` |
| One-page-per-image for review tooling | `hankpdf in.pdf -o page.webp --output-format webp --image-dpi 150` |
| Preview thumbnails | `hankpdf in.pdf -o thumb.jpg --output-format jpeg --image-dpi 72 --jpeg-quality 60` |
| Single-process serial run for debugging | `hankpdf in.pdf -o out.pdf --max-workers 1` |

## Wall-time scaling

Roughly: wall-time is **dominated by per-page raster + classify + compose + verify**, not by total bytes. A 30-page input at default settings ran in 28s; a 190-page input took 28s as well — page count is the lever, not file size, when the per-page workload is similar.

`--mode fast` cuts wall time by ~2-3× by lowering source render DPI; quality at letter-page sizes is unchanged.

## Reproducing

```bash
# Adjust paths in /tmp/hankpdf-bench/run_bench.py for your inputs.
uv run python /tmp/hankpdf-bench/run_bench.py
column -t -s $'\t' /tmp/hankpdf-bench/results.tsv
```

The benchmark script lives at `/tmp/hankpdf-bench/run_bench.py` (not committed — it points at user-local files). For a committed reference, see `tests/fixtures/smoke_text.pdf` regenerated by `scripts/make_smoke_fixture.py`.
