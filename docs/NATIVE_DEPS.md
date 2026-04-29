# Native Dependencies

HankPDF depends on a handful of native libraries. This doc tracks (a) where each comes from at runtime, (b) how we build / bundle it for tests and CI, and (c) what changes between supported platforms.

User-facing install instructions live in `INSTALL.md`. This doc is for contributors.

## Runtime deps by category

### Bundled via Python wheel (no user action)
These arrive automatically with `pip install hankpdf`:

| Dep | Provider wheel | Notes |
|---|---|---|
| pdfium | `pypdfium2` | bblanchon/pdfium-binaries bundled inside the wheel; weekly upstream cadence |
| OpenJPEG | `Pillow` (libjpeg2000 via jpeg2k plugin) | bundled; we don't install OpenJPEG separately |
| libjpeg-turbo | `Pillow` | bundled |
| Leptonica | transitively via Tesseract at runtime, via `opencv-python-headless` for some paths |  |
| zlib | system or `Pillow` | always available |

### User-installed (system package manager)
These must exist on PATH or at well-known locations:

| Dep | Floor | Reason for floor |
|---|---|---|
| Tesseract | 5.0 | LSTM engine; legacy engine optional |
| qpdf | **11.6.3** | qpdf #1050 silent-character-drop bug in 11.0.0–11.6.2 |
| jbig2enc | pinned commit (vendored build) | upstream unmaintained; we vendor for reproducibility |

### CI-built from source (tests only)
For tests that exercise codecs the wheels can't cover:

| Dep | Source |
|---|---|
| jbig2enc | `https://github.com/agl/jbig2enc` pinned commit; built with AFL++ fuzz corpus in CI |

## Platform notes

### Linux
- Debian / Ubuntu: apt packages work for Tesseract + qpdf; jbig2enc we ship via `jbig2enc-tools` if available else vendored build.
- RHEL / Fedora: dnf packages work for Tesseract + qpdf; jbig2enc we always vendor.
- Alpine: glibc-free; not a primary target. Use the `debian-slim` Docker image.

### macOS
- Homebrew provides Tesseract + qpdf. jbig2enc we vendor and build from source.
- Apple Silicon: all our wheels ship arm64 variants.

### Windows
- Chocolatey / Scoop / winget provide Tesseract + qpdf.
- jbig2enc has no reliable Windows package manager source. We ship a prebuilt `jbig2.exe` as a GitHub Release asset; `INSTALL.md` points users to download it.
- Alternatively: tell users to use Docker Desktop. Simpler.

### Docker image
All deps baked in, including jbig2enc from our vendored source. `docker run` requires nothing on the host.

## `--doctor` verification

`hankpdf --doctor` locates every dep at startup and prints:

- Dep name, version, resolution path
- Our floor version (if any)
- PASS / FAIL per dep
- Remediation hint on FAIL

Exits 0 on all-pass; 41 on any floor violation.

## Dependency-upgrade policy

- Critical CVEs (remote code execution in a parser) — upgrade ASAP, release patch.
- Non-critical CVEs — upgrade on the next scheduled release.
- Feature / minor upgrades — Dependabot PR; weekly CI corpus run catches rendering-fidelity regressions before merge.
- Major-version bumps (API break) — manual review, documented migration.
