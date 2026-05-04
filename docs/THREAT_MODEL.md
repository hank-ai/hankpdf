# Threat Model

HankPDF is a local command-line tool: PDF in, compressed PDF out. It runs on the user's machine, makes no outbound network calls during compression, and stores no persistent state beyond what the user asks for.

The threat model below inventories attack surfaces and mitigations. High level: the only real attack surface is a **hostile input PDF tripping a third-party parser**, and the mitigation is process-level sandboxing plus careful dep hygiene.

## Assets

| Asset | What we care about |
|---|---|
| User's PDF content | Integrity (no silent drift), confidentiality (no exfiltration) |
| User's host machine | Availability (not OOM'd), integrity (not RCE'd via parser) |
| Our release artifacts (PyPI wheel, GHCR image) | Provenance (users can verify we signed them) |

## Attackers

| Attacker | Capability | In-scope? |
|---|---|---|
| Hostile PDF author | Crafts malformed PDF, hopes victim runs HankPDF on it | **Yes** — primary threat |
| Malicious dependency upstream | Ships malicious code inside pdfium / Tesseract / qpdf etc. | Partial — relies on upstream CVE hygiene + pinning + OIDC provenance |
| Compromised contributor | Adds a backdoor via PR | **Yes** — CI lint + code review + no merge without green build |
| Compromised release machine | Our own build machine exfiltrates data or publishes malicious wheel | Partial — mitigated by GitHub OIDC trusted publishing (no long-lived tokens); out of scope for a self-hosted build |
| Network attacker | MitM between user and PyPI / GHCR | Partial — mitigated by TLS + PyPI / GHCR's own integrity checks |

## Attack surfaces & mitigations

### 1. Hostile PDF → parser RCE / memory corruption

```
Hostile PDF → pdfium or qpdf or Tesseract or jbig2enc → RCE → escape to user's host
```

**Mitigations:**
- **Wall-clock and per-page watchdogs** (implemented): `per_page_timeout_seconds` is enforced via `future.result(timeout=…)` (serial) and `as_completed(timeout=…)` (parallel); `total_timeout_seconds` is checked at phase boundaries via `_check_total_timeout`. Raises typed `PerPageTimeoutError` / `TotalTimeoutError` / `OcrTimeoutError` (exit codes `[E-TIMEOUT-PER-PAGE]`, `[E-TIMEOUT-TOTAL]`, `[E-OCR-TIMEOUT]`).
- **Subprocess resource caps via RLIMIT_AS + Job Object** (implemented as of 0.3.0): per-page workers self-apply a memory cap on init. Linux/macOS uses `resource.setrlimit(RLIMIT_AS)` (macOS kernel rejects this; the watchdog backstops); Windows ≥ 8 uses ctypes against `kernel32.dll` to assign each worker to a Job Object with `JOB_OBJECT_LIMIT_PROCESS_MEMORY` + `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. Default cap is `min(max(8 GB, 16 × input_size), 16 GB)`, further clamped by an aggregate-envelope check against `psutil.virtual_memory().available × 0.7 / n_workers`. A parent-side psutil RSS watchdog runs as a redundant backstop with cooperative shutdown (workers check a shared `mp.Event` at safe-write boundaries; never SIGTERM mid-write). RLIMIT_CPU per-page CPU cap remains planned for a future release. See `hankpdf/sandbox/platform_caps.py` and `_compute_worker_mem_cap` in `hankpdf/__init__.py`.
- **DoS caps** (implemented): `--image-dpi ≤ 1200`, `--pages` cardinality ≤ 1 M, `--max-workers ≤ 256`, sub-byte `--max-output-mb` rejected at parse time, timeout flags validated > 0.
- **Decompression-bomb guard** (implemented): `PIL.Image.MAX_IMAGE_PIXELS` set at import in `hankpdf/_pillow_hardening.py` to `MAX_BOMB_PIXELS` (~715 Mpx); Pillow's `DecompressionBombError` is translated to our typed exception and routes to exit `[E-INPUT-DECOMPRESSION-BOMB]` (exit 16). A pre-allocation pixel-budget check in `hankpdf/engine/image_export.py` computes `target_w × target_h` from page geometry *before* rasterization — not after.
- **JBIG2 decoder hardening** (implemented): we never re-decode existing `/JBIG2Decode` streams outside the sandbox (ForcedEntry / CVE-2021-30860 attack class).
- **Ghostscript excluded** from the stack: historically the worst CVE offender plus AGPL-problematic.
- **Docker image** (partial): non-root user is implemented (`docker/Dockerfile`). Read-only rootfs and baked seccomp profile are listed for Phase 6 in `docker/README.md` but **not yet applied** — the image is still a Phase-0 skeleton. See `docs/ROADMAP.md` §T4.7.

### 2. Hostile PDF → silent content drift

```
Hostile PDF → compresses successfully → output has subtly wrong content → user relies on it
```

**Mitigations:**
- **Content-preservation verifier** (implemented, opt-in): OCR Levenshtein diff ≤ 2%, reading-order-insensitive bag-of-lines Levenshtein, global SSIM ≥ 0.92, tile-level min SSIM ≥ 0.85, **digit-multiset exact match** on numeric tokens, structural audit (page count, annots, forms). The verifier is **off by default** (`skip_verify=True`). Pass `--verify` to enable. When skipped, the CLI emits a `[W-VERIFIER-SKIPPED]` banner on stderr so users know verification did not run; the report carries `status="skipped"` and a `verifier-skipped` warning code. To treat skipped verification as a hard failure in automation, check for exit code `[E-VERIFIER-FAIL]` or assert `report.verifier.status != "skipped"`.
- **Safe mode** tightens thresholds and escalates to human review on any tile SSIM < 0.96.
- **Signed PDFs**: refuse by default (`[E-INPUT-SIGNED]`). Explicit opt-in required. Certifying signatures (`/Perms /DocMDP`) need a second, stricter flag (`[E-INPUT-CERTIFIED-SIGNED]`).

### 3. Hostile PDF → JBIG2 6↔8 substitution (Xerox bug class)

**Mitigations:**
- JBIG2 generic region coding only. Symbol mode and refinement flag (`-r`) are **absent from the wrapper**, not merely defaulted off.
- `--legal-mode` / `legal_codec_profile="ccitt-g4"` forces CCITT G4 instead of JBIG2 for users who require BSI / NARA compliance. **Note**: `legal_codec_profile` raises `NotImplementedError` in the current build — this is a planned feature (Phase 3). The `--legal-mode` CLI flag is accepted but the engine guard will reject it. See `docs/ROADMAP.md`.

### 4. Password leakage

```
User passes password → ends up in ps output, core dump, or log
```

**Mitigations:**
- Passwords never on argv (verified): `--password-file PATH` or `HANKPDF_PASSWORD` env var only. No `--password <string>` flag exists in the CLI.
- Password buffer zeroed on process exit.
- `PR_SET_DUMPABLE=0` on Linux child to prevent core-dump leakage (planned — no implementation found in this build; sandbox subprocess not yet written).

### 5. Content leakage via logs

```
User pipes logs to a server → /Title, /Author, OCR text end up in a log store
```

**Mitigations:**
- Filenames hashed when logged (`sha1(basename)[:8]…basename[-8:]`).
- OCR text, `/Title`, `/Author`, `/Subject`, `/Keywords`, `/Producer` never logged verbatim.
- CI lint rule bans `logger.info(f"...{filename}...")` and similar patterns.

### 6. Release artifact tampering

```
Attacker compromises our PyPI / GHCR / GitHub release → ships malicious wheel
```

**Mitigations:**
- **PyPI trusted publishing via GitHub OIDC** — no long-lived API tokens that could leak.
- **GHCR publishes via GitHub OIDC**. Each pushed image is cosign-signed (keyless, same OIDC issuer) and carries a SLSA v1 build-provenance attestation + SPDX SBOM. Consumers pin by digest and verify via `cosign verify` + `gh attestation verify`.
- **Dockerfile pins the base image by digest** (`python:3.14-slim-trixie@sha256:…`). Every apt package is pinned to an explicit version. Dependabot's docker + github-actions + pip ecosystems propose bumps.
- **Workflows pin every action by commit SHA** (not tag) so a compromised tag on `actions/*` can't silently change what we run.
- **Windows release assets** ship SHA-256 sidecars next to every download, plus SLSA provenance. The installer script refuses to install if the sidecar is missing or mismatched.
- **`.github/versions.json` is the single source of truth** for native-dep pins (jbig2enc commit, qpdf floor, Leptonica floor). CI fails if the SHA appears anywhere else in the repo (grep-and-fail).
- **GitHub Releases include SHA-256 checksums** for every artifact + image digests; users can verify.

### 7. Supply chain: hostile dependency upgrade

```
Upstream gets compromised → we pull in a malicious version via `pip install`
```

**Mitigations:**
- **Dep floor pins** on the sharp edges (qpdf ≥ 11.6.3, OpenJPEG ≥ 2.5.4).
- **Dependabot** opens PRs for upgrades; CI runs the golden corpus before merge.
- **Weekly pypdfium2 canary** measures rendering-fidelity drift on upgrades.
- **No AGPL / GPL deps** — permissive only, reduces the legal-plus-technical surface.
- **jbig2enc vendored** (upstream unmaintained) with a known-good commit hash.

### 8. Compromised contributor (PR injection)

**Mitigations:**
- CI green required before merge (lint + mypy + unit tests + integration tests).
- Review required on every PR.
- No merge access on `main` without review.
- Pre-commit hooks run ruff + mypy on every local commit.

## Out of scope

- DoS via deliberately pathological PDFs that trip our resource caps. SIGKILL on cap exceed is working-as-designed, not a bug. User can try again with adjusted limits or accept the refusal.
- Side-channel attacks on the user's machine (power analysis, timing of CPU cache misses, etc.).
- Physical access to the user's machine.
- Vulnerabilities in the user's OS, shell, terminal emulator, or downstream tooling that consumes our output.

## Update cadence

This doc gets reviewed:
- On any new CVE in our dep chain.
- On any change to the engine pipeline that adds a new attack surface.
- Annually as a clean sweep.

**Last reviewed**: 2026-05-02 — updated for v0.3.0 production-safety hardening release. Key changes from this release: RLIMIT_AS / Job Object per-page memory caps are now **implemented** across Linux, macOS, and Windows (was: planned). Decompression-bomb regression corpus added; structured refusals wired for huge-MediaBox, xref-loop, and ObjStm-explosion fixtures. RLIMIT_CPU per-page CPU caps, read-only rootfs / seccomp Docker hardening, `PR_SET_DUMPABLE` on Linux, and CCITT G4 legal-mode codec **remain planned** for future releases.
