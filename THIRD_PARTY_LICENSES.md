# Third-Party Licenses & Commercial-Use Audit

HankPDF (`hankpdf`) is licensed under **Apache-2.0** (see [LICENSE](LICENSE)).
This file enumerates every third-party dependency — Python packages, native
binaries invoked via subprocess, and their transitive system libraries —
along with the SPDX license identifier and commercial-use status.

**Bottom line: every dependency in the tree below is commercial-use-safe** when
used as HankPDF uses it (Python imports, subprocess to system binaries
installed separately via the OS package manager). Last audited: 2026-04-23.

The tool chain was chosen specifically to avoid the commercial-licensing
blockers common in PDF/OCR tooling (Ghostscript AGPL, Poppler GPL, ABBYY /
Nuance commercial licensing). See [§ Deliberately avoided](#deliberately-avoided)
below.

---

## Python runtime dependencies (pip-installed)

| Package | SPDX license | Commercial-use |
|---|---|---|
| pypdfium2 | `Apache-2.0 AND BSD-3-Clause` | ✅ |
| pikepdf | `MPL-2.0` | ✅ library use — see [MPL-2.0 note](#mpl-20-pikepdf) |
| Pillow | `HPND` (MIT-CMU) | ✅ |
| numpy | `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0` | ✅ |
| opencv-python-headless | `Apache-2.0` | ✅ |
| scikit-image | `BSD-3-Clause` | ✅ |
| scipy | `BSD-3-Clause` | ✅ |
| imageio, tifffile, lazy-loader, networkx | `BSD-2-Clause` / `BSD-3-Clause` | ✅ |
| lxml | `BSD-3-Clause` | ✅ |
| deprecated | `MIT` | ✅ |
| packaging | `Apache-2.0 OR BSD-2-Clause` | ✅ |
| wrapt | `BSD-2-Clause` | ✅ |
| typing-extensions | `PSF-2.0` | ✅ |
| pytesseract | `Apache-2.0` | ✅ |

## Native binaries (subprocess-called)

These are installed by the OS package manager (Homebrew on macOS,
`apt`/`yum` on Linux). HankPDF invokes them via `subprocess` — it does not
statically link them, bundle their binaries, or redistribute them.

| Binary | SPDX license | Purpose |
|---|---|---|
| tesseract | `Apache-2.0` | OCR |
| qpdf | `Apache-2.0` (pre-v7 dual-licensed Artistic-2.0) | PDF sanitize / linearize |
| jbig2enc | `Apache-2.0` | 1-bit mask compression |

Tesseract language data (`eng.traineddata`, etc.) ships from Google's
official `tessdata` repositories under `Apache-2.0`.

## Libraries bundled inside Python wheels

Pillow and pypdfium2 ship with pre-built native libraries inside their
Python wheels. These are the ones HankPDF actually uses:

| Library | SPDX license | Bundled in |
|---|---|---|
| pdfium | `BSD-3-Clause` | pypdfium2 |
| OpenJPEG | `BSD-2-Clause` | Pillow (for JPEG2000) |
| libjpeg-turbo | `IJG AND Zlib AND BSD-3-Clause` | Pillow |
| zlib / zlib-ng | `Zlib` | Pillow |
| libpng | `libpng-2.0` (BSD-style) | Pillow |
| freetype | `FTL` (BSD-style) | Pillow |
| littlecms2 | `MIT` | Pillow |
| libwebp | `BSD-3-Clause` | Pillow |
| libavif | `BSD-2-Clause` | Pillow |
| harfbuzz | `MIT` | Pillow |

All permissive. No re-linking or LGPL accommodation required when
HankPDF is shipped as a standard `pip install`.

## Transitive system libraries (Homebrew deps of tesseract)

Tesseract on Homebrew dynamically links the following. HankPDF never imports
or modifies them — they live as separate shared libraries on disk that
tesseract loads at runtime.

| Library | SPDX license | Notes |
|---|---|---|
| harfbuzz | `MIT` | permissive |
| leptonica | `BSD-2-Clause` | permissive |
| libarchive | `BSD-2-Clause` | permissive |
| libjpeg-turbo | `IJG AND Zlib AND BSD-3-Clause` | permissive |
| webp | `BSD-3-Clause` | permissive |
| icu4c | `ICU` (Unicode license) | permissive |
| fontconfig | `HPND-sell-variant AND Unicode-3.0 AND MIT AND MIT-Modern-Variant` | permissive |
| freetype | `FTL` | permissive |
| **cairo** | `LGPL-2.1-only OR MPL-1.1` | see [LGPL / GPL note](#lgpl--gpl-transitive-notes) |
| **glib** | `LGPL-2.1-or-later` | see [LGPL / GPL note](#lgpl--gpl-transitive-notes) |
| **pango** | `LGPL-2.0-or-later` | see [LGPL / GPL note](#lgpl--gpl-transitive-notes) |
| **gettext** | `GPL-3.0-or-later AND LGPL-2.1-or-later` | see [LGPL / GPL note](#lgpl--gpl-transitive-notes) |

---

## MPL-2.0 (pikepdf)

**MPL-2.0** is a weak / file-level copyleft. Commercial use as a library is
**unrestricted**. The constraint kicks in only if HankPDF **modifies the
pikepdf source files themselves** — in that case, the modified pikepdf files
would need to be made available under MPL-2.0. HankPDF imports pikepdf as a
dependency without modification, so MPL-2.0 imposes no obligation beyond
preserving the copyright notice (which `NOTICE` handles).

Do **not** fork pikepdf in-tree. If a pikepdf bug needs a fix, upstream it.

---

## LGPL / GPL transitive notes

Tesseract links dynamically against several LGPL libraries — `glib`,
`pango`, `cairo` — and against gettext's LGPL `libintl` runtime. **LGPL
explicitly permits closed-source / commercial use with dynamic linking**
(that is the entire point of the "L" in LGPL). Homebrew / apt / yum ship
these as separate `.dylib`/`.so`/`.dll` files that tesseract loads at
runtime. That arrangement satisfies LGPL's re-linking requirement
automatically — users can replace the shared library without rebuilding
tesseract.

gettext's GPL-3.0 components (`msgfmt`, `xgettext`, `gettextize`) are
**build-time-only** tools for compiling `.po` translation files. They are
not linked into tesseract's runtime and are never invoked by HankPDF.

### What this means for you

✅ **OK:** ship HankPDF source / pip-installable package. Users run
`brew install tesseract` or `apt-get install tesseract-ocr` themselves.
No LGPL exposure — you never distribute the LGPL libs.

✅ **OK:** Docker image that installs tesseract via the base image's
package manager. LGPL libs stay as separate shared-library files; users
can `docker exec` in and replace them.

⚠ **Requires care:** building a single-binary distribution that
statically links tesseract + glib + pango + cairo into one executable
(e.g., `pyinstaller --onefile`, a static Go wrapper, an AOT-compiled bundle).
Then the LGPL clause kicks in — you would need to ship either
(a) the LGPL libs as separate replaceable `.so`/`.dylib`/`.dll` files, or
(b) object files + build scripts so users can re-link their own copy. Not
hard to comply with, but it requires intent.

❌ **Not OK:** bundle `msgfmt` / `xgettext` / other gettext tools into a
distribution. Those are GPL-3.0 and would infect the whole bundle. HankPDF
never invokes them, so this would only happen by accident.

---

## Deliberately avoided

Tools excluded specifically because they would block commercial use:

| Tool | License | Problem |
|---|---|---|
| **Ghostscript** | `AGPL-3.0` (or commercial license from Artifex) | AGPL infects network-served applications. Artifex's commercial license is expensive. |
| **Poppler** (`pdftocairo` / `pdftoppm`) | `GPL-2.0-or-later` | Static linking or distributing modified versions would force HankPDF to GPL. |
| **pdftoppm / pdftotext from xpdf** | `GPL-2.0` | Same as Poppler. |
| **ABBYY FineReader Engine** / **OmniPage / Nuance OCR** | Commercial, per-seat | Per-page licensing fees. |
| **iText / iTextSharp v7+** | `AGPL-3.0` (or commercial license) | AGPL infects SaaS. |

HankPDF replaces all of these with permissive-license equivalents:
pdfium (BSD) for rendering, tesseract (Apache) for OCR, pikepdf (MPL
library-use) + qpdf (Apache) for PDF manipulation, jbig2enc (Apache) for
JBIG2 encoding.

---

## Attribution & redistribution checklist

When you cut a release or ship to a customer:

- [x] `LICENSE` — Apache-2.0 full text ← included
- [x] `NOTICE` — Apache-2.0 attribution file ← included
- [x] `THIRD_PARTY_LICENSES.md` — this document ← included
- [ ] If you ship a Docker image: include `/usr/share/doc/*/copyright` from
      tesseract and friends, or equivalently copy this file into the image.
- [ ] If you ship a single-binary distribution (PyInstaller --onefile,
      statically linked wrapper, etc.): revisit the
      [LGPL / GPL note](#lgpl--gpl-transitive-notes) above.
- [ ] If you fork a dependency: preserve the upstream license and respect
      any copyleft obligations (pikepdf's MPL-2.0 is the only file-level
      copyleft in the tree; everything else is permissive).

---

*Re-audit cadence: before every release, or whenever a runtime dep is
added / upgraded to a major version. If a new transitive dep appears
under GPL / AGPL, that's a blocker that must be resolved before shipping.*
