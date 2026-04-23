# Threat Model

HankPDF is a local command-line tool: PDF in, shrunk PDF out. It runs on the user's machine, makes no outbound network calls during compression, and stores no persistent state beyond what the user asks for.

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
- All engine work runs in a **child subprocess** bounded by `RLIMIT_AS` (memory), `RLIMIT_CPU` (per-page), and a wall-clock watchdog. Crash / RCE is scoped to the child.
- **Decompression-bomb guard**: `PIL.Image.MAX_IMAGE_PIXELS` set explicitly; exceed → `DecompressionBombError` with exit 16.
- **JBIG2 decoder hardening**: we never re-decode existing `/JBIG2Decode` streams outside the sandbox (ForcedEntry / CVE-2021-30860 attack class).
- **Ghostscript excluded** from the stack: historically the worst CVE offender plus AGPL-problematic.
- **Docker image** adds defense-in-depth: non-root user, read-only rootfs, baked seccomp profile, no network by default.

### 2. Hostile PDF → silent content drift

```
Hostile PDF → compresses successfully → output has subtly wrong content → user relies on it
```

**Mitigations:**
- **Content-preservation verifier** runs on every page: OCR Levenshtein diff ≤ 2%, reading-order-insensitive bag-of-lines Levenshtein, global SSIM ≥ 0.92, tile-level min SSIM ≥ 0.85, **digit-multiset exact match** on numeric tokens, structural audit (page count, annots, forms).
- **Safe mode** tightens thresholds and escalates to human review on any tile SSIM < 0.96.
- **Signed PDFs**: refuse by default. Explicit opt-in required. Certifying signatures (`/Perms /DocMDP`) need a second, stricter flag.

### 3. Hostile PDF → JBIG2 6↔8 substitution (Xerox bug class)

**Mitigations:**
- JBIG2 generic region coding only. Symbol mode and refinement flag (`-r`) are **absent from the wrapper**, not merely defaulted off.
- `--legal-mode` / `legal_codec_profile=True` forces CCITT G4 instead of JBIG2 for users who require BSI / NARA compliance.

### 4. Password leakage

```
User passes password → ends up in ps output, core dump, or log
```

**Mitigations:**
- Passwords never on argv. `--password-file PATH` or `HANKPDF_PASSWORD` env var only.
- Password buffer zeroed on process exit.
- `PR_SET_DUMPABLE=0` on Linux child to prevent core-dump leakage.

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
- **GHCR publishes via GitHub OIDC**.
- **GitHub Releases include SHA-256 checksums** for every artifact + image digests; users can verify.
- No separate code-signing infrastructure because we don't ship platform binaries — OIDC provenance is the integrity chain.

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
