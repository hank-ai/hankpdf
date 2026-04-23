# Security Policy

## Reporting a vulnerability

If you think you've found a security issue in HankPDF, **please do not open a public GitHub issue**. Report it privately:

- GitHub Security Advisories (once the repo is public) — preferred: https://github.com/ourorg/pdf-smasher/security/advisories/new
- Email: security@TBD.example

We'll acknowledge within 72 hours and aim for a triage decision within a week. Coordinated disclosure preferred.

## Scope

HankPDF is a local command-line tool. It runs on the user's machine, does not open network sockets during compression, and does not transmit user data anywhere. The security-relevant attack surfaces are:

1. **Parser RCE / memory corruption** via hostile PDF input — the engine runs third-party parsers (pdfium, qpdf, Tesseract, jbig2enc, OpenJPEG, OpenCV). Report anything that escapes our process-level sandbox.
2. **Silent content loss** — anything that causes the verifier to pass an output whose content does not match the input (e.g. adversarial inputs that bypass the SSIM + OCR-diff gates).
3. **Privilege or data exfiltration via the engine subprocess** — anything that causes the engine to read or write files outside its designated input / temp / output paths.

Out of scope:
- DoS via deliberately-pathological PDFs that trip our resource caps (SIGKILL on `RLIMIT_AS` exceedance is working as designed).
- Vulnerabilities in dependencies we've already updated past — report those upstream.

## Release integrity

We don't ship platform-native binaries and don't use code-signing CAs. Instead:

- **PyPI** — `pip install pdf-smasher` uses PyPI's checksum + GitHub OIDC trusted-publishing provenance.
- **Docker** — `ghcr.io/ourorg/pdf-smasher@sha256:…` pins to immutable digests.
- **GitHub Releases** — SHA-256 checksums for wheel and sdist are published in every release note. Image digests are included.

Users can verify downloaded artifacts against the published checksums; the release-metadata signing uses GitHub's attestation infrastructure.

## Dependency policy

- Permissive licenses only (Apache-2.0 / BSD / MPL-2.0 / MIT).
- Floor-pinned versions for known-critical deps:
  - qpdf ≥ 11.6.3 (pre-11.6.3 has a silent character-drop bug, qpdf #1050)
  - OpenJPEG ≥ 2.5.4 (CVE-2025-54874 OOB heap write in ≤2.5.3)
  - Tesseract 5.x with pinned tessdata pack SHA-256
- Weekly CVE-scan job; Dependabot PRs auto-merged after CI green for non-breaking bumps.

## Attack surface we explicitly mitigate

- **Hostile PDF parsing** — every invocation runs in a child subprocess with `RLIMIT_AS`, `RLIMIT_CPU`, and wall-clock watchdog. Docker image adds non-root user, read-only rootfs, and a baked seccomp profile.
- **JBIG2 decoder** — we never re-decode existing `/JBIG2Decode` streams outside the sandbox (per the ForcedEntry / CVE-2021-30860 attack class).
- **Digital signatures** — refuse-by-default policy; explicit opt-in flag (`--allow-signed-invalidation`) for regular signatures, separate stricter flag (`--allow-certified-invalidation`) for certifying signatures.
- **Decompression bombs** — `PIL.Image.MAX_IMAGE_PIXELS` set explicitly; exceeds → structured refusal with exit 16.
- **Password leakage** — passwords never on argv (ps-visible). `--password-file` or `HANKPDF_PASSWORD` env var only; held in a buffer zeroed on exit; `PR_SET_DUMPABLE=0` on Linux.
