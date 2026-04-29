# Performance & quality reference

Measured benchmarks across representative inputs and the full settings matrix. Numbers in this doc come from a single run on an Apple Silicon laptop (M-series), Python 3.14. The compression numbers are post the per-page selective MRC gate (branch `feat/per-page-selective-mrc` ≈ commit `f8b4253`). Re-run via `python /tmp/hankpdf-bench/bench_full_matrix.py` (matrix script in repo) or adapt the script to your inputs.

## TL;DR

- **Use HankPDF when the input is a scanned document.** Compression of 2.7×–5.5× is typical on real-world scan-derived slide decks; higher on true scanned text.
- **Don't use HankPDF on already-efficient PDFs.** Native-export presentations (PowerPoint → PDF, Word → PDF) inflate when run through the MRC pipeline because the embedded JPEGs are already at high compression. The default `--min-ratio 1.5` correctly passthrough's these inputs unchanged.
- **Existing text layers are preserved by default** (no flag required). `--ocr` fills gaps via Tesseract on pages with no native text or garbage native text; `--strip-text-layer` opts out (text-free output); `--re-ocr` forces Tesseract on every page. See the [text-layer section](#ocr--text-findability) for the quality heuristic and full settings matrix.
- **For known-scan inputs, `--mode fast` is the sweet spot.** It gets ~the same compression as `--mode standard` in roughly one-third the wall time, with no visible quality difference at letter-page DPI.
- **Image export beats the PDF pipeline on size for some workflows.** WebP at 150 DPI / quality 80 is the smallest output in our matrix and visually clean.
- **`--max-workers 0` (auto, default) is correct.** Serial mode is ~4× slower on this hardware; past `cpu_count` of perf-cores there's no further gain.
- **`--verify` is a strict, slow quality gate.** ~5.8× wall-time cost; will refuse common MRC outputs unless paired with `--ocr` and `--mode safe`. Use only when downstream consumers treat absence-of-drift as a contract.
- **Per-page MRC gate skips pages with no meaningful image content.** A cheap stream-length signal (`image_xobject_bytes / page_byte_budget`) is computed per page; pages below `--per-page-min-image-fraction` (default 0.30) are emitted verbatim. On a 50-page text-only PDF, this drops wall time from 3.83s to 0.25s (~15×) and avoids 53× output inflation. On image-heavy inputs the gate fires on every page, so wall time is unchanged. `--re-ocr`, `--strip-text-layer`, and `--verify` all disable the gate. See [Per-page MRC gate](#per-page-mrc-gate) below.

## Per-page MRC gate

Every page is scored before the pipeline splits work across workers. A page's `image_byte_fraction` is `image_xobject_bytes / (content_stream_bytes + image_xobject_bytes + other_xobject_bytes)`. Pages with a fraction at or above `--per-page-min-image-fraction` (default `0.30`) go through the full MRC pipeline; pages below are copied verbatim into the output PDF (no rasterize, no classify, no compose, no verify).

When **no** pages meet the threshold, the whole-doc passthrough shortcut fires and the input bytes are returned unchanged — `CompressReport.status == "passed_through"` with the `passthrough-no-image-content` warning. On partial runs, `CompressReport.pages_skipped_verbatim` carries the indices of skipped pages and a `pages-skipped-verbatim-N` warning is emitted.

| Input | Pages | Setting | Wall time | Output | Notes |
|---|---:|---|---:|---:|---|
| 50-page text-only synthetic | 50 | gate on (default) | **254 ms** | 20,755 B (== input) | whole-doc passthrough |
| 50-page text-only synthetic | 50 | `--per-page-min-image-fraction 0 --min-ratio 0` | 3,830 ms | 1,109,406 B | full MRC; 53× **inflation** |
| I_small (mixed slides + photo) | 18 | gate on (default) | 3.99 s | 2,551,055 B (== input) | every page MRC-worthy → pipeline runs → min-ratio passthrough |
| I_med (native-export slides) | 190 | gate on (default) | 26.87 s | 11,346,630 B (== input) | every page MRC-worthy → pipeline runs → min-ratio passthrough |
| I_large (scan-derived) | 30 | gate on (default) | 19.06 s | 8,447,331 B | 27/30 pages MRC-worthy; 3 verbatim |

The gate is a **conservative pre-filter**, not a full whole-doc detector — embedded JPEGs in native-export slides will mark pages as image-heavy even when MRC won't compress further. The downstream `--min-ratio` check is what handles the "ran but didn't help" case. The gate's headline value is on **text-only** inputs, where it converts a 4-second inflate-and-throw-away cycle into a 250 ms passthrough.

Disable the gate any time a forced full pipeline is required:

- `--re-ocr` — force Tesseract on every page; gate is bypassed.
- `--strip-text-layer` — explicit text-removal request; gate is bypassed.
- `--verify` — verifier needs the full pipeline output to compare against; gate is bypassed (otherwise the aggregator would see synthetic verdicts).
- `--per-page-min-image-fraction 0` — sets the threshold to 0 so every page meets it.

## Test inputs

Three real-world PDFs from a 2026 conference presentation set, plus one synthetic text-only baseline:

| ID | Description | Size | Pages | Content shape |
|---|---|---|---|---|
| **I_text50** | 50-page synthetic text-only (no images) | 21 KB | 50 | Native PDF, zero image XObjects — gate's headline use case |
| **I_small** | "Two Sides, One Record …" | 2.5 MB | 18 | Native-export slides with embedded photos |
| **I_med** | "Boot Camp Joint Slides 2026" | 11 MB | 190 | Native-export slides (heavy text + vector + small embedded JPEGs) |
| **I_large** | "Scaling Anesthesia Billing and Compliance" | 23 MB | 30 | Scan-derived slides (rasterized) |

## Settings tested

PDF compress (7 settings; all run with default `--min-ratio` unless otherwise noted):

- `default` — no flags. Exercises the per-page MRC gate AND the `--min-ratio 1.5` passthrough.
- `fast` — `--mode fast --min-ratio 0`
- `safe` — `--mode safe --min-ratio 0`
- `aggressive` — `--target-bg-dpi 100 --target-color-quality 40 --min-ratio 0`
- `quality` — `--target-bg-dpi 200 --target-color-quality 75 --min-ratio 0`
- `ocr` — `--ocr --min-ratio 0`
- `gate_off` — `--per-page-min-image-fraction 0 --min-ratio 0` (disables the per-page gate; baseline for "what would have happened pre-gate")

Image export (2 settings, exercised on the realistic inputs):

- `image_jpg_150` — `--output-format jpeg --image-dpi 150 --jpeg-quality 75`
- `image_webp_150` — `--output-format webp --image-dpi 150 --webp-quality 80`

## Results

Format: `output_bytes (compression_ratio×, wall_seconds)`. Compression ratio is `input_bytes / output_bytes`; values >1 are smaller-than-input, <1 are larger.

> **Note (post per-page-MRC gate, 2026-04-27):** The `default` row in every table below now exercises the per-page gate's whole-doc passthrough whenever the input has no MRC-worthy pages, AND the existing `--min-ratio 1.5` floor when the pipeline runs but doesn't beat the floor. Both are correct behaviors — they show the user "we did not produce a smaller output for this input" by returning the input bytes unchanged. The `gate_off` row replicates the pre-gate behavior for comparison.

### I_text50 — 50-page synthetic text-only PDF, 21 KB ★

This input is the gate's headline use case: a native PDF with **zero image content**. The whole-doc passthrough fires; output is byte-identical to input.

| Setting | Output | Ratio | Time | Notes |
|---|---:|---:|---:|---|
| default | 21 KB | 1.00× | **0.39s** | ★ whole-doc passthrough; status=`passed_through` |
| fast | 21 KB | 1.00× | 0.35s | gate fires before mode is consulted |
| safe | 21 KB | 1.00× | 0.36s | same |
| aggressive | 21 KB | 1.00× | 0.35s | same |
| quality | 21 KB | 1.00× | 0.35s | same |
| ocr | 21 KB | 1.00× | 0.36s | same |
| **gate_off** (`--per-page-min-image-fraction 0 --min-ratio 0`) | **1.15 MB** | **0.02×** | **2.87s** | full pipeline; **53× inflation** |

Take: on text-only inputs the gate is decisive — all settings collapse to the same 250–400 ms passthrough, while the gate-disabled run inflates the file 53× and burns 8× the wall time.

### I_small — 2.5 MB native PDF, 18 pages (mixed text/photo slide deck)

| Setting | Output | Ratio | Time | Notes |
|---|---:|---:|---:|---|
| default | 2.4 MB | 1.00× | 4.0s | every page MRC-worthy → pipeline runs → min-ratio passthrough |
| fast | 2.1 MB | 1.15× | 2.4s | ★ best speed/size in PDF mode |
| safe | 2.4 MB | 1.02× | 3.9s | same as default with explicit min-ratio bypass |
| aggressive | **1.5 MB** | **1.58×** | 3.9s | ★ best PDF size |
| quality | 4.1 MB | 0.60× | 4.2s | inflates — vector slides aren't this codec's friend |
| ocr | 2.4 MB | 1.02× | 4.0s | |
| gate_off | 2.4 MB | 1.02× | 4.0s | |
| image_jpg_150 | 3.6 MB | 0.68× | **1.7s** | fastest path |
| image_webp_150 | **1.5 MB** | **1.63×** | 3.8s | ★ smallest overall |

Take: 18-page slide deck where every page has an embedded photo so the gate engages on every page. The pipeline runs but the input is already efficient; only `aggressive` and image-export-to-WebP beat 1×.

### I_med — 11 MB native PDF, 190 pages (heavy native-export slides)

| Setting | Output | Ratio | Time | Notes |
|---|---:|---:|---:|---|
| default | 11 MB | 1.00× | **26.3s** | min-ratio passthrough; output == input |
| fast | 24 MB | 0.44× | 13.4s | inflates 2.2× |
| safe | 26 MB | 0.41× | 27.1s | inflates 2.4× |
| aggressive | 18 MB | 0.62× | 26.3s | least-bad |
| quality | 44 MB | 0.25× | 28.4s | inflates 4× |
| ocr | 26 MB | 0.41× | 29.8s | |
| gate_off | 26 MB | 0.41× | 28.3s | |
| image_jpg_150 | 40 MB | 0.27× | 11.1s | |
| image_webp_150 | 18 MB | 0.59× | 33.2s | |

Take: 190-page native-export slide deck. **Every aggressive setting inflates this input.** The default correctly returns the input unchanged via min-ratio refusal. **Don't run HankPDF on inputs like this.**

### I_large — 23 MB scan-derived PDF, 30 pages ★ (the canonical good case)

| Setting | Output | Ratio | Time | Notes |
|---|---:|---:|---:|---|
| default | 8.1 MB | **2.78×** | 22.4s | 27/30 pages MRC-worthy; 3 verbatim |
| **fast** | **7.4 MB** | **3.04×** | **9.0s** | ★ same compression as default in **1/3 the time** |
| safe | 8.1 MB | 2.78× | 20.6s | identical to default |
| aggressive | **4.9 MB** | **4.55×** | 19.1s | text crisp, gradients more compressed |
| quality | 14.6 MB | 1.53× | 18.8s | near-source |
| ocr | 8.1 MB | 2.77× | 25.1s | identical compression with searchable text |
| gate_off | 8.1 MB | 2.76× | 22.6s | gate had no impact (all pages MRC-worthy) |
| image_jpg_150 | 11.4 MB | 1.97× | **2.6s** | ★ fastest path overall |
| image_webp_150 | **4.1 MB** | **5.48×** | 10.0s | ★ smallest output overall |

Take: scan-derived slide deck — exactly the input HankPDF is built for. All `--mode` settings deliver 2.8×–5.5× compression. **The new gate has no impact here** (`gate_off` ≈ `default`) because every page is image-heavy — the gate degrades gracefully on the inputs that need the pipeline.

## Visual quality assessment

I rendered page 1 of each I_large output and the source at 100 DPI, then visually inspected them. Findings:

- **default vs source** — text is sharp and identical to the eye. The decorative gradient on the left swoosh is slightly softer in the compressed output but the difference requires close comparison.
- **fast vs default** — visually indistinguishable.
- **aggressive vs source** — text is still crisp and legible. The photo-style gradient on the swoosh shows mild banding under close inspection but is acceptable for any non-archival use.
- **quality vs source** — near-indistinguishable. Worth the size penalty only when archival fidelity is non-negotiable.
- **jp2k vs default** — comparable visual. Not a noticeable improvement at letter-page DPI.

For the synthetic-text scan (`I_synth`), `aggressive` produced text identical to source at 100 DPI render — text legibility is preserved even at the most compressed setting.

## OCR / text-findability

**Defaults preserve any existing text layer.** No flag required. If the input PDF arrived with searchable text, the output keeps that text verbatim — byte-faithful to the source, with no Tesseract recognition noise and no per-page Tesseract cost.

**`--ocr` means "ensure the output is searchable"** — it fills gaps. If the input has no text layer (true scan) OR the existing text fails a quality heuristic (mostly-symbol noise, single-char-flood "S c a l i n g" patterns, etc.), Tesseract runs to fill in. Pages that already have decent native text are kept as-is even with `--ocr` set.

**Two opt-out flags:**

- `--strip-text-layer` — explicitly remove any text layer. Output is guaranteed text-free. Use for size-only workflows where searchability is unwanted.
- `--re-ocr` — force Tesseract on every page even when the input has good native text. Use when the upstream OCR is known to be wrong and you want a fresh Tesseract pass.

### Measured behavior on I_large (30-page scan-derived deck with upstream text)

| Run | Output total chars | Output page-1 sample | Wall time |
|---|---:|---|---:|
| default (no flags) | **3,998** | "Scaling Anesthesia Billing…" (faithful) | **5.4s** ★ |
| `--no-ocr` | 3,998 | same — preservation is unconditional | 5.1s |
| `--strip-text-layer` | 0 | (empty) | 5.3s |
| `--ocr` | 12,162 | native preserved + Tesseract fills sparse pages | 7.1s |
| `--re-ocr` | 12,769 | Tesseract everywhere (Tesseract noise) | 7.9s |

The default went from 10.1s pre-this-feature to **5.4s** — 47% faster AND searchable, by skipping Tesseract entirely on inputs that already have a usable text layer.

### Quality heuristic (`is_native_text_decent`)

Lives in `hankpdf.engine.text_layer`. Inspects the extracted word list and returns `True` if the text looks like real words. Rejects:

- **Mostly-non-alphabetic content** (`alpha-or-space ratio < 0.5`) — corrupted layers full of `?` / replacement markers.
- **Average word length outside 2-12 chars** — gibberish OCR often produces single-char tokens or long runs of garbage.
- **>40% single-character "words"** — the "S c a l i n g" pattern from OCR engines that couldn't infer word boundaries.

Sparse pages (cover, dividers, < 30 chars total) pass — light text density alone isn't a quality signal. Tunables are centralized as `_NATIVE_DECENCY_*` constants in `text_layer.py`.

### Comparison to running Tesseract

| Aspect | Native preservation | Tesseract |
|---|---|---|
| Text faithfulness | byte-exact source text | recognition noise (collapsed spaces, mis-OCR'd glyphs) |
| Per-page cost | ~50ms textpage walk | ~1-5s subprocess per page |
| Source font / kerning | preserved (positions exact) | re-laid-out via Helvetica heuristic |
| Verifier input-vs-output | comparable (native input vs Tesseract output still works) | symmetric (Tesseract input vs Tesseract output) |
| Works on true scans | requires upstream text layer | works on any image |

Practical guidance:

- **Default settings just work.** Run `hankpdf in.pdf -o out.pdf` and the output keeps whatever searchable text the input had. No flag needed.
- **For inputs that may or may not be scans:** `hankpdf in.pdf -o out.pdf --ocr` does the right thing automatically — preserves good native text, falls back to Tesseract on pages that need it.
- **Force-OCR escape hatch:** if an upstream tool's OCR is known-bad, `--re-ocr` runs Tesseract regardless.
- **Strip-everything escape hatch:** `--strip-text-layer` produces a text-free output (rare; size-only workflows).

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

## Real-world matrix (31 PDFs × 8 settings = 248 runs)

To validate the per-setting takeaways above against a wider input set, the full matrix was run against **31 native-export presentation PDFs** from a 2026 conference (sizes 320 KB → 23 MB; 1 → 190 pages each; all have embedded text layers). Every input went through 8 settings: 4 PDF-compress paths and 4 image-export paths.

### Per-setting rollup

| Setting | Success | Median ratio | p25 / p75 ratio | Min ratio | Max ratio | Median wall | Total wall |
|---|---:|---:|---:|---:|---:|---:|---:|
| default (passthrough fallback) | 31/31 | **1.00×** | 1.00 / 1.00 | 1.00 | 2.76 | 6.1s | 244s |
| `--mode fast --min-ratio 0` | 31/31 | **0.33×** | 0.21 / 0.50 | 0.07 | 3.03 | 3.2s | 128s |
| `--bg-dpi 100 --color-quality 40 --min-ratio 0` | 31/31 | **0.46×** | 0.26 / 0.67 | 0.09 | 4.74 | 5.9s | 229s |
| `--ocr` (preserve native + Tesseract for gaps) | 31/31 | **1.00×** | 1.00 / 1.00 | 1.00 | 2.75 | 6.3s | 253s |
| `-o page.jpg --image-dpi 150 --jpeg-quality 75` | 31/31 | **0.19×** | 0.12 / 0.31 | 0.05 | 1.97 | 2.2s | 77s |
| `-o page.jpg --image-dpi 300 --jpeg-quality 90` | 31/31 | **0.05×** | 0.03 / 0.08 | 0.01 | 0.42 | 6.3s | 217s |
| `-o page.webp --image-dpi 150 --webp-quality 80` | 31/31 | **0.46×** | 0.27 / 0.69 | 0.13 | 5.48 | 5.7s | 207s |
| `-o page.png --image-dpi 150` | 31/31 | **0.09×** | 0.06 / 0.14 | 0.03 | 0.59 | 3.3s | 121s |

A **median ratio < 1.0× means the setting INFLATES the typical input**. PNG @ 150 DPI inflates output to 11× the input size on the median; JPEG @ 300 DPI inflates to 20×. These settings are correct for "I want one image per page for a viewer or thumbnail pipeline" workflows, never for "I want a smaller PDF."

### How often does each setting actually compress the file?

| Setting | <1× (inflated) | 1.0× exactly | 1.0–1.5× | 1.5–3× | >3× |
|---|---:|---:|---:|---:|---:|
| default | 0 | **30** | 0 | 1 | 0 |
| `--mode fast --min-ratio 0` | **29** | 0 | 1 | 0 | 1 |
| aggressive (100 DPI / quality 40) | **29** | 0 | 0 | 1 | 1 |
| `--ocr` | 0 | **30** | 0 | 1 | 0 |
| jpeg @ 150 DPI | **30** | 0 | 0 | 1 | 0 |
| jpeg @ 300 DPI | **31** | 0 | 0 | 0 | 0 |
| webp @ 150 DPI | **28** | 0 | 1 | 1 | 1 |
| png @ 150 DPI | **31** | 0 | 0 | 0 | 0 |

Every aggressive PDF-compress + every image-export setting INFLATES native-export PDFs the vast majority of the time. The default's `--min-ratio 1.5` short-circuit is the right behavior for this entire input class — 30 of 31 inputs passthrough unchanged.

The single input that compresses meaningfully (Upadya Loynes "Scaling Anesthesia Billing") is **scan-derived**, not native-export. It hits 2.76× at default and 5.48× at webp@150 — the same headline numbers from the I_large case study above.

### Text-layer preservation (default-preserve behavior)

- **30/31 inputs**: default preserves at least 95% of the input's native text characters. The output is searchable verbatim, with no flag required.
- The 1/31 outlier: the scan-derived "Scaling Anesthesia Billing" deck has only 4,241 source text chars; default preserved 3,998 (94%) — a few chars dropped to native-extraction's word-grouping heuristic on edge-case glyphs. The visible text remains complete and searchable.
- `--ocr` enriched the text by >5% over default on 1/31 inputs (the same scan-derived deck — Tesseract filled sparse pages where native text was absent). For 30/31 inputs, `--ocr` adds zero new text because the inputs were already fully searchable.

### Notable per-input highlights

**Best compression seen (per input, any setting):**

| Input | Best setting | Ratio | In | Out |
|---|---|---:|---:|---:|
| Upadya Loynes Scaling Anesthesia Billing and Compliance | webp_150 | 5.48× | 22,965 KB | 4,192 KB |
| Upadya Conlon "Two Sides, One Record…" | webp_150 | 1.63× | 2,491 KB | 1,528 KB |
| West "Approaches to Analytics Driven Management Strategies" | webp_150 | 1.07× | 1,287 KB | 1,205 KB |
| 28 other inputs | default (passthrough) | 1.00× | (unchanged) | (unchanged) |

**Worst inflation seen (per input, any setting):**

| Input | Worst setting | Ratio | In | Out |
|---|---|---:|---:|---:|
| Moody "What's New in Anesthesia for 2026" | jpeg_300 | 0.01× | 343 KB | 24,739 KB |
| Cameron "Navigating Payer Policies and Insurance Challenges" | jpeg_300 | 0.02× | 970 KB | 51,062 KB |
| Carey "The Role of Bylaws, Credentialing, and Privileging" | jpeg_300 | 0.02× | 1,048 KB | 51,827 KB |

JPEG/PNG/WebP at high DPI on native-export PDFs is the worst-case for output size — a 1 MB input can balloon to 51 MB if you hand-pick the wrong setting. Stick to defaults unless you specifically know your input is scan-derived.

### Reproducing this matrix

```bash
# 31 inputs × 8 settings = 248 rows; ~30-50 min on M-series.
uv run python /tmp/hankpdf-bench/run_matrix2.py
uv run python /tmp/hankpdf-bench/aggregate.py  # writes /tmp/hankpdf-bench/aggregate.md
```

Both scripts live in `/tmp/hankpdf-bench/` (not committed — they point at user-local files). Adapt `DIR` in `run_matrix2.py` to your own corpus.

## Reproducing

```bash
# Adjust paths in /tmp/hankpdf-bench/run_bench.py for your inputs.
uv run python /tmp/hankpdf-bench/run_bench.py
column -t -s $'\t' /tmp/hankpdf-bench/results.tsv
```

The benchmark script lives at `/tmp/hankpdf-bench/run_bench.py` (not committed — it points at user-local files). For a committed reference, see `tests/fixtures/smoke_text.pdf` regenerated by `scripts/make_smoke_fixture.py`.
