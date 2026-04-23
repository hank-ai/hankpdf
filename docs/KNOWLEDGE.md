# HankPDF — Knowledge Reference

Background material that informs the design but isn't strictly architecture or spec. Use as a reference when a decision needs justifying or re-opening.

## 1. The compression problem, in one diagram

```
Input (scanned color 300 DPI, 8.5×11 page)
  = 2550 × 3300 × 3 bytes raw  ≈ 25 MB / page decoded

Embedded in PDF as JPEG quality 90
  ≈ 2–4 MB / page

200-page document      ≈ 400–800 MB

Target output          ≈ 4–15 MB total
                       ≈ 20–80 KB / page
                       ≈ 100–500× compression vs. raw
                       ≈ 20–200× compression vs. high-quality JPEG

How we get there: stop treating the page as one image.
```

## 2. MRC — Mixed Raster Content

The algorithmic heart of the engine.

### 2.1 The insight

A scanned page is not a "picture"; it's a composition of layers with very different statistical properties:

- **Text / line art**: binary (ink vs no ink), high spatial frequency, sharp edges, needs high resolution to remain legible but compresses superbly as 1-bit.
- **Background / paper / photos**: smooth tonal gradients, low spatial frequency, compresses superbly at low resolution with lossy DCT/wavelet codecs.

Treating a page as a single JPEG applies the wrong codec to both components simultaneously:
- JPEG at low quality destroys text edges (DCT ringing around high-contrast transitions).
- JPEG at high quality wastes bytes on paper texture and photo halftones.

MRC separates the two. A well-segmented mask is the difference between 10× and 100× compression.

### 2.2 Standards and foundational work

- **ITU-T T.44** (1999): Mixed Raster Content standard. Public spec, free to download from ITU. Defines the layered model: Foreground, Background, Mask, each with independent codec.
- **DjVu** (1998, AT&T Labs): Léon Bottou, Patrick Haffner, Yann LeCun. Papers describe pixel-level foreground/background decomposition, lossy-bitonal layer refinement, wavelet background encoding (IW44). The DjVu libraries are GPL, but the published techniques are fair game.
  - Bottou et al., "High Quality Document Image Compression with DjVu," Journal of Electronic Imaging, 1998.
  - Haffner et al., "Browsing through High Quality Document Images with DjVu," IEEE Forum on Research and Technology Advances in Digital Libraries, 1998.
- **JPEG2000 Part 6 (JPM)** (ISO/IEC 15444-6): file format profile for MRC using JPEG2000 codec. Less commonly implemented; PDF/MRC compatibility is easier.
- **Lossy MRC in PDF**: PDF 1.4 added the `/SMask` (Soft Mask) construct to image XObjects. Two image XObjects + a soft mask = a readable MRC page in any PDF reader dating to 2001.

### 2.3 Our specific algorithm

**Per-page strategy selector runs first.** Not all pages get MRC:

| Class | Detection | Strategy |
|---|---|---|
| Already-optimized | Existing `/JBIG2Decode`+`/SMask` pattern, or DCT quality <40 | Pass-through |
| Text-only | Mask coverage >95%, bg variance <5% | Single JBIG2 page image |
| Photo-only | Mask coverage <5%, uniform high-frequency bg | Single JPEG2000 page image |
| Mixed | Default | Full MRC below |

**CMYK pre-pass** (before rasterization): pdfium renders CMYK via a naive non-ICC lookup; for CMYK inputs we use littlecms (lcms2) for managed sRGB conversion to preserve colors accurately.

**Full MRC pipeline for mixed pages:**

1. **Rasterize** via pdfium at working DPI (default 300). Always held under the process-global pdfium lock.
2. **Small-print check** (pre-OCR): histogram connected-component x-heights. If p50 <12 px, upscale 2× (`cv2.INTER_CUBIC`) or route page to full-JPEG safe mode. Prevents Tesseract silently dropping glyphs <8 px x-height.
3. **Mask generation**:
   - Tesseract hOCR → word-level bounding boxes + baselines.
   - OpenCV adaptive threshold (Sauvola or Niblack) → candidate foreground pixels.
   - Union: word-box fill + adaptive-threshold pixels outside word boxes (catches line art, signatures, rule lines).
   - Morphological close (3×3 or 5×5 kernel) to smooth edges.
   - **Handwriting-region supplement**: connected-components + stroke-width variance heuristic to seed mask regions Tesseract won't return (Tesseract's LSTM doesn't OCR handwriting reliably).
   - Output: 1-bit mask, **same dimensions as the foreground layer** (Quartz/Preview drops SMasks with dimension mismatch).
4. **Foreground layer**:
   - Per-region ink color (median per connected component); global median for pure black-text pages as an optimization.
   - **JBIG2 generic region coding only** — no symbol mode (Xerox 6/8 substitution), no refinement `-r` flag (crashes Acrobat).
   - For color-text pages: foreground is a low-DPI RGB image under the same mask.
5. **Background layer**:
   - Inpaint mask-1 pixels (nearest-neighbor or Gaussian-hole-fill).
   - Downsample to target DPI (150 default; 100 for `mode=fast`, 200 for `mode=safe`).
   - Encode as JPEG2000 (OpenJPEG) at calibrated 0–100 quality, or JPEG with **`subsampling=0` (4:4:4)** — not Pillow's default 4:2:0, which smears colored text.
6. **Compose PDF page** using the `/SMask` pattern:
   ```
   Page content stream:
     Background image (full page, low-DPI)
     Foreground image (RGB or 1-bit, high-DPI)
       /SMask -> mask
   ```
   All image XObjects carry explicit `/ColorSpace [/ICCBased <sRGB>]`; document has a single sRGB `/OutputIntent`.
7. **Invisible OCR text layer** (text rendering mode 3). Positions from Tesseract hOCR. Searchable, no visual effect.
8. **Legal/archival profile** (optional): swap JBIG2 for CCITT Group 4 on 1-bit layer (BSI TR-03138 / NARA compliance).

### 2.4 Where ratios come from

Order-of-magnitude contribution on a color scan typical input:

| Step | Cumulative compression |
|---|---|
| Input (high-quality JPEG page) | 1× |
| Separate into layers (no re-encode yet) | 1× |
| Downsample background 300→150 DPI | 4× |
| Background at JPEG quality 55 | ~2.5× |
| Foreground as 1-bit JBIG2 (vs 8-bit gray) | ~8× on the foreground pixels |
| Mask as 1-bit JBIG2 | negligible size |
| Color→mono detection on text-only pages | up to 10× on those pages |

Multiplicative: 4 × 2.5 × (weight × 8) on a mixed color page typically lands at 15–30× total. Text-only pages go to 100×+ because the color/gray background layer effectively disappears.

### 2.5 Where we differ from Internet Archive's `archive-pdf-tools`

`archive-pdf-tools` uses the same algorithm. Differences (mostly driven by licensing):

- We don't link or use any AGPL code. We read the published algorithm and implement from scratch.
- We use pdfium for rasterization (Apache-2.0) instead of Ghostscript or MuPDF.
- We use OpenCV's thresholding rather than archive-pdf-tools' custom grayscale heuristics.
- We emphasize the verifier pipeline — archive-pdf-tools does not have a formal content-preservation gate.

## 3. Codec reference

### 3.1 JBIG2

Joint Bi-level Image Group standard (ITU-T T.88 / ISO/IEC 14492). Successor to CCITT G4 for 1-bit images. Compresses bi-tonal data significantly better than G4.

**Three modes:**
- **Generic region coding**: context-adaptive arithmetic coding. Lossless. What we use.
- **Symbol mode**: dictionary of recurring symbols (letters, graphics), each occurrence becomes a reference. Much better compression for text.
- **Pattern mode**: halftone dictionaries.

**Why we don't use symbol mode.** Symbol substitution in lossy mode is the source of the 2013 Xerox scanner bug (dkriesel.com) — digits 6 and 8 got silently swapped in scanned documents for years. Refinement coding (`-r` in jbig2enc) triggers Adobe Acrobat crashes. For a tool that will see medical and legal content, the extra 20% compression from symbol mode is not worth any whiff of silent corruption.

**Encoder**: `jbig2enc` by Adam Langley (Apache-2.0). Stable; small C codebase (~2k LOC). We vendor it, build with our CI, run a fuzz corpus (AFL++) on malformed input in CI.

### 3.2 JPEG2000

ISO/IEC 15444. Wavelet-based. Better than JPEG at low bitrates (what we want for heavy bg downsampling), native alpha channel support, better resilience to high compression.

**Encoder**: OpenJPEG (BSD-2-Clause).

**Reader compatibility**: Universal in modern readers (Adobe Acrobat 6+, macOS Preview, pdfium, MuPDF). Acrobat 5 is irrelevant in 2026.

**Trade-off**: ~2–4× slower to encode than JPEG. Acceptable for a background pipeline; arguable on a user's laptop. We default to JPEG2000, allow JPEG via `--bg-codec jpeg`.

### 3.3 JPEG

ISO/IEC 10918. Everyone knows it. Supported forever.

**Encoder**: libjpeg-turbo (BSD).

**Use case**: fallback background codec when JPEG2000 is undesirable (regulated environments that require universal ancient-reader support).

**Chroma subsampling gotcha**: Pillow's JPEG default is **4:2:0 subsampling at quality <91**. On our mixed-content background — which contains colored text that the mask didn't capture, colored highlighter, red "VOID" stamps — 4:2:0 smears color detail. We **force 4:4:4 (`subsampling=0`)** for mixed-content backgrounds. Cost: ~15–25% larger JPEG. Benefit: colored text stays legible. For photo-only pages (the strategy selector routes these separately anyway), 4:2:0 is fine.

**Quality scale**: Pillow's `quality=55` ≠ mozjpeg's `quality=55`. We calibrate on our own corpus; the `target_color_quality` option is 0–100 calibrated against visible difference on documents, not a direct pass-through to libjpeg's quality dial.

### 3.4 Reader compatibility matrix (MRC output)

| Reader | JBIG2 | JPEG2000 | `/SMask` | Notes |
|---|---|---|---|---|
| Adobe Acrobat | ✅ (generic region; avoid symbol mode, refinement `-r`) | ✅ | ✅ | Full spec support. Slow first-render on multi-page JBIG2. |
| macOS Preview / Quartz | ✅ | ✅ | ⚠️ drops SMask on dimension mismatch | Mask must match foreground dimensions exactly. OCRmyPDF #1536 is the canonical bug. |
| Chrome / pdfium | ✅ | ✅ | ✅ | Production-grade. |
| Firefox / pdf.js | ⚠️ blank pages reported | ⚠️ broken colors in some streams | ⚠️ regressions in 2.13–2.16 | Known issues #19517, #14701, #18400. For a Firefox-preview path, consider CCITT G4 fallback. |
| Microsoft Edge | ✅ (uses pdfium) | ✅ | ✅ | Same as Chrome. |
| Foxit | ✅ (proprietary decoder) | ✅ | ✅ | Silently renders blank on unparseable. Test explicitly. |
| MuPDF | ✅ | ✅ | ✅ | |

Rule of thumb: our output must match mask dimensions exactly, set explicit sRGB color space on every image XObject, include a sRGB OutputIntent, and declare PDF 1.7 header minimum.

### 3.4 CCITT Group 4

ITU-T T.6. 1-bit fax encoding. Universally supported.

**Use case**: fallback for 1-bit content in environments where JBIG2 is not trusted. Baseline comparison for JBIG2 claims.

### 3.5 Codec decision matrix

| Layer | Primary | Fallback |
|---|---|---|
| Mask (1-bit) | JBIG2 generic | CCITT G4 |
| Foreground (1-bit) | JBIG2 generic | CCITT G4 |
| Foreground (color text, rare) | JPEG2000 low-DPI | JPEG |
| Background | JPEG2000 | JPEG |
| OCR text layer | PDF text rendering mode 3 (invisible) | — |

## 4. PDF internals crash-course

Just enough to understand the edge cases in [SPEC.md §4](SPEC.md).

### 4.1 Structure

A PDF is a sequence of objects (dicts, streams, arrays, numbers, strings). Objects are referenced by `(object_number, generation)` tuples. A **cross-reference table** (xref) at the end of the file maps references to byte offsets. A **trailer** points to the root `/Catalog` dict.

Corrupt xref → recoverable by scanning the file from the start and rebuilding the object table. qpdf does this automatically.

### 4.2 Content streams and XObjects

A page `/Contents` is a stream of PDF operators (text, paths, images). Images are stored as `/XObject /Subtype /Image` referenced from the content stream via `Do` operator.

Form XObjects (`/Subtype /Form`) are sub-page content streams — vector drawings, diagrams, logos. We must **not** rasterize them in Recompress, or we lose the vector fidelity.

### 4.3 Filters

Streams are compressed with `/Filter` (can chain):
- `/FlateDecode` — zlib
- `/DCTDecode` — JPEG
- `/JPXDecode` — JPEG2000
- `/CCITTFaxDecode` — CCITT G4
- `/JBIG2Decode` — JBIG2
- `/LZWDecode` — legacy
- `/ASCII85Decode`, `/ASCIIHexDecode` — escaping

Triage enumerates filter chains to detect `/JBIG2Decode` (don't re-decode; see §5.3) and `/DCTDecode` with visible low quality (already optimized, pass through).

### 4.4 Encryption

PDF supports RC4-40, RC4-128, AES-128, AES-256. Handled via `/Encrypt` dict in the trailer. Two keys: **user** (opens the file) and **owner** (removes restrictions). If opening without a password succeeds but operations are restricted, the file is owner-encrypted only — we proceed.

Certificate-based encryption (`/Filter /Adobe.PPKLite`) uses X.509 certificates. We refuse these.

### 4.5 Signatures

Digital signatures (`/Type /Sig`) cover a byte range of the file via PKCS#7. Any modification to those bytes invalidates the signature. Re-compressing always invalidates.

**Pass-through gotcha**: pikepdf's default `Pdf.save()` rewrites the whole file (reorders objects, regenerates `/ID`), which invalidates signatures even when we didn't touch content. To pass through signed PDFs untouched we use pikepdf's incremental-update mode — writes new objects after the original xref instead of rewriting it. The original signed bytes stay exactly where they were, signature validates.

**Certifying signatures** (`/Perms /DocMDP`): stricter than regular signatures — they legally certify the document and have stronger legal consequence. We require a separate `--allow-certified-invalidation` flag; the normal `--allow-signed-invalidation` does not cover them.

Our policy:
- Regular signature detected, no opt-in → pass-through via incremental save.
- Regular signature, `--allow-signed-invalidation` → recompress; strip `/SigFlags` bit 1 to avoid "broken signature!" viewer warnings.
- Certifying signature, no opt-in → refuse (`CertifiedSignatureError`, exit 15).
- Every invalidation audit-logged to the sidecar manifest with timestamp + local username + input SHA-256.

### 4.6 Linearization ("web optimization")

A linearized PDF can be incrementally loaded by a reader over the web. It has specific byte-ordering constraints. We delinearize, recompress, then re-linearize via `qpdf --linearize`.

### 4.7 PDF/A

Archive-grade PDF profile. PDF/A-1b, A-2b/2u, A-3, A-4 are distinct levels with different constraints.

- **PDF/A-2u** is our preferred output: allows JPEG2000, allows Unicode OCR text, disallows embedded files, disallows transparency beyond simple cases.
- **PDF/A-3** allows arbitrary embedded files, which re-opens the "embedded-Trojan" attack surface — we don't target this as output.

Conformance validation: **veraPDF** (Mozilla Public License 2.0 — permissive). Run as a smoke test in CI.

### 4.8 Tagged PDFs (accessibility)

`/StructTreeRoot` + `/MarkInfo /Marked true` means the PDF has a logical structure tree for screen readers. Screen-reader users navigate these tags. We preserve tags; pass-through by default for tagged PDFs because aggressive mask/recompression disturbs tag geometry. Aggressive mode explicitly rebuilds tag geometry from Tesseract word bounds (untested at v1 — future work).

## 5. License rationale

Every dep was chosen for a shipping-compatible license. Summary of each:

| License | Examples | Effect on us |
|---|---|---|
| Apache-2.0 | pdfium, pypdfium2, qpdf, Tesseract, OpenCV, scikit-image (deps via BSD-3 elsewhere) | Permissive. Must include license + NOTICE file. Patent grant. Compatible with closed-source. |
| BSD-2-Clause / BSD-3-Clause | OpenJPEG, NumPy, scikit-image, Leptonica | Permissive. Attribution required. No patent grant (lower but non-zero risk). |
| MPL-2.0 | pikepdf | Weak copyleft at **file** granularity. Modifications to pikepdf source must be published, but our code linking to pikepdf does not need to be. Compatible with closed-source products. |
| MIT / HPND | Pillow | Fully permissive. |

**Banned:** GPL (any variant). LGPL needs legal review (dynamic linking may be OK; static is not). AGPL is strictly excluded.

## 6. Prior art

### 6.1 archive-pdf-tools (Internet Archive)

Reference implementation of MRC in Python. AGPL-3.0, so we can't copy the code, but the published algorithm and quantified expectations are public.

Key benchmarks reported:
- 3–15× typical compression ratio on scanned images.
- Up to 249× on a prepared test image.
- Single-threaded Python performance: ~1.16 s/page MRC with pre-computed hOCR; ~11.4 s/page when Tesseract runs in-band (at 400 DPI, single core).

### 6.2 OCRmyPDF

Excellent Python wrapper around Tesseract + pikepdf + (historically) Ghostscript. MPL-2.0. Does not do MRC segmentation — it runs `--optimize 3` via Ghostscript's downsample, achieving ~2–3× compression on scans. Our advantage: we add MRC, so we get 10–50× more.

### 6.3 DjVu

Historical DjVu encoders (djvulibre) are GPL. Format is technically different from PDF but the compression approach is nearly identical to MRC-in-PDF.

### 6.4 Commercial MRC

Foxit PDF Compressor (ex-CVISION, ex-LuraTech) and ABBYY FineReader Server dominate the commercial MRC market. Both closed-source, both expensive, both priced by sales quote. Benchmark references:
- Foxit claims 8–10× on color scans.
- ABBYY claims similar, with stronger OCR.

Neither is distributable at customer-laptop scale in a royalty-free manner. Neither gives us control over the compression algorithm.

## 7. CVE history — the parsers we do and don't use

### 7.1 Ghostscript (NOT USED)

- **CVE-2024-29510**: format-string exploitation in `uniprint` device → SAFER bypass → RCE. Actively exploited in the wild.
- **CVE-2024-29506 / 29507 / 29509**: buffer overflows.
- **CVE-2024-29511**: arbitrary file read/write.

Ghostscript has a long history of RCE-class vulnerabilities and SAFER-bypass chains. Combined with AGPL licensing, it's the single worst choice for our threat model.

### 7.2 MuPDF (NOT USED)

- Heap overflows in `fz_append_display_node` (1.15).
- Infinite-loop DoS in `pdf_parse_array` (1.12).
- Smaller CVE list than Ghostscript but still substantial. AGPL-licensed by Artifex.

### 7.3 pdfium (USED)

- Chromium security team actively fuzzes and fixes.
- Non-trivial CVE cadence — 2024–2026 saw **CVE-2024-5846** (UAF), **CVE-2024-5847** (UAF), **CVE-2024-7973** (heap OOB read), **CVE-2026-2648**, **CVE-2026-5287** (UAF), **CVE-2026-5889** (crypto / encryption bypass), **CVE-2026-6305**, **CVE-2026-6306** (heap overflows). Eight CVEs in a 12-month window.
- Threat model designed for "render untrusted PDFs safely in a browser," which is exactly our scenario.
- Permissive license.
- **Operational consequence**: we run a **weekly pypdfium2 upgrade canary** in CI that auto-opens a PR with rendering-drift measurements on our golden corpus. Pinning to a 6-month-old revision = known-exploitable worker.
- **Separately**: pdfium's own **JBIG2 decoder** (inherited into pdfium from old Chromium code) has had issue-tracker RCE class bugs. Combined with the ForcedEntry (CVE-2021-30860) lesson: we never re-decode JBIG2 streams outside the sandbox. If input contains a `/JBIG2Decode` stream, we pass it through opaquely.

### 7.4 qpdf (USED)

- Designed explicitly for *repair*, not rendering. Much smaller attack surface.
- **CVE-2024-24246**: heap buffer overflow in qpdf 11.9.0 on crafted JSON — DoS + possible RCE. Advisory GHSA-6733-f273-8q48.
- Historical: heap overflow in `QPDF::processXRefStream` (8.4.2), `Pl_ASCII85Decoder::write` (9.x–10.0.4), integer-overflow-driven OOB read in `Pl_Buffer::write` (PNG filter).
- **Data-loss bug (not a security CVE but equally important)**: qpdf **11.0.0–11.6.2 silently dropped the byte after 1- or 2-digit octal escapes (`\d`, `\dd`) inside binary strings** — corrupted `/ID`, XMP metadata in encrypted files, bookmark names, form-field values, even encryption keys. Launchpad #2039804 / qpdf #1050. **Fixed in 11.6.3** — we pin this as a hard floor in `--doctor`.

### 7.5 OpenJPEG (USED)

- **CVE-2025-54874**: OOB heap write in `opj_jp2_read_header` when `p_stream` too short and `p_image` not initialized. Triggered by malformed input to *decoder*; our encode path is safe but downstream readers using an older OpenJPEG are vulnerable to other malicious PDFs.
- Numerous historical CVEs, mostly decoder-side.
- We pin OpenJPEG ≥ 2.5.4 for our build.

### 7.6 Leptonica (transitively via Tesseract and jbig2enc, USED)

- **CVE-2018-7186**, **CVE-2020-36277** historical. We pin ≥ 1.82 and track.

### 7.5 JBIG2 decoders (extra caution)

- **CVE-2021-30860** (ForcedEntry / Pegasus): CoreGraphics JBIG2 decoder integer-overflow → weaponized by NSO Group into a Turing-complete exploit framework. Google Project Zero called it "one of the most technically sophisticated exploits ever seen."
- Takeaway: never re-decode JBIG2 streams outside a sandbox. Our policy — pass through existing `/JBIG2Decode` streams opaquely rather than re-decoding — neutralizes this class.

## 8. Data handling and distribution posture

HankPDF is a local tool. We don't receive PDFs, don't store them, don't route them through infrastructure we control. "Compliance posture" for this product is mostly about:

### 8.1 What we do NOT touch

- No user PDFs ever cross our systems.
- No telemetry, analytics, crash reports, or usage data are collected.
- No tenant identifiers, no user accounts, no logins.
- We carry no BAA because there's no Business Associate relationship — we're software, not a service.

### 8.2 What touches our systems

Only release artifacts. Third-party services we rely on for build and distribution, none of which ever see user PDFs:
- **PyPI** — hosts our Python wheel + sdist. Uploaded via GitHub OIDC trusted publishing (no long-lived tokens). Downloads are anonymous GETs.
- **GitHub Container Registry (GHCR)** — hosts our Docker image. Pushed via GitHub OIDC.
- **GitHub Releases** — hosts signed release notes + SHA-256 checksums.

No code-signing CAs, no Apple notary service — we don't ship platform-native binaries, so none of that applies.

### 8.3 Logging hygiene (still matters locally)

Even though logs stay on the user's machine, users often forward them when debugging or embed HankPDF in pipelines that collect logs centrally. Content-hygiene rules keep those downstream log stores clean too:

- **Never log** raw filenames, OCR text, PDF content, passwords, `/Title`, `/Author`, `/Subject`, `/Keywords`, `/Producer`, embedded metadata.
- **Always hash filenames** when referencing them in logs — `hankpdf.utils.log.redact_filename()` → `sha1(basename)[:8]…basename[-8:]`.
- **Use job IDs** (UUIDs) as the correlation handle.
- **Structured JSON** log format (optional) with a fixed schema, so downstream collectors can filter and redact consistently.
- **CI lint rule** bans `logger.info(f"...{filename}...")` and any log call with f-strings containing `path`, `filename`, `basename`, `producer`, `ocr_text`, `content`. Everything routes through the `redact_*` helpers.

## 9. Sources for further reading

### MRC / document-image compression
- ITU-T T.44 (1999). https://www.itu.int/rec/T-REC-T.44
- Bottou et al., "High Quality Document Image Compression with DjVu," J. Electronic Imaging (1998).
- Haffner et al., "DjVu: Analyzing and Compressing Scanned Documents for Internet Distribution," ICDAR (1999).

### PDF spec
- ISO 32000-1:2008 (PDF 1.7). https://www.iso.org/standard/51502.html
- ISO 32000-2:2020 (PDF 2.0).
- PDF Reference (Adobe), sixth edition — searchable but unofficial.

### Libraries and tools
- pdfium: https://pdfium.googlesource.com/pdfium/
- pypdfium2: https://pypdfium2.readthedocs.io
- qpdf: https://qpdf.readthedocs.io
- pikepdf: https://pikepdf.readthedocs.io
- Tesseract: https://tesseract-ocr.github.io
- jbig2enc: https://github.com/agl/jbig2enc
- OpenJPEG: https://www.openjpeg.org
- OpenCV: https://opencv.org
- scikit-image: https://scikit-image.org
- OCRmyPDF (reference wrapper): https://ocrmypdf.readthedocs.io
- archive-pdf-tools (reference MRC impl): https://github.com/internetarchive/archive-pdf-tools

### Security
- JBIG2 Xerox incident, Kriesel (2013): https://www.dkriesel.com/en/blog/2013/0802_xerox-workcentres_are_switching_written_numbers_when_scanning
- Project Zero on NSO FORCEDENTRY: https://googleprojectzero.blogspot.com/2021/12/a-deep-dive-into-nso-zero-click.html
- Ghostscript CVE-2024-29510 analysis (Codean Labs).
- veraPDF (PDF/A conformance validator): https://verapdf.org
