# Security Policy

## Reporting a vulnerability

If you think you've found a security issue in HankPDF, **please do not open a public GitHub issue**. Report it privately:

- GitHub Security Advisories (once the repo is public) — preferred: https://github.com/hank-ai/hankpdf/security/advisories/new

We'll acknowledge within 72 hours and aim for a triage decision within a week. Coordinated disclosure preferred.

## Scope

HankPDF is a local command-line tool. It runs on the user's machine, does not open network sockets during compression, and does not transmit user data anywhere. The security-relevant attack surfaces are:

1. **Parser RCE / memory corruption** via hostile PDF input — the engine runs third-party parsers (pdfium, qpdf, Tesseract, jbig2enc, OpenJPEG, OpenCV). Report anything that escapes our process-level sandbox.
2. **Silent content loss** — anything that causes the verifier to pass an output whose content does not match the input (e.g. adversarial inputs that bypass the SSIM + OCR-diff gates).
3. **Privilege or data exfiltration via the engine subprocess** — anything that causes the engine to read or write files outside its designated input / temp / output paths.

Out of scope:
- DoS via deliberately-pathological PDFs that trip our resource caps (SIGKILL on `RLIMIT_AS` exceedance is working as designed).
- Vulnerabilities in dependencies we've already updated past — report those upstream.

## Operational assumptions

- The output directory passed via `-o`/`--output-dir` is assumed to be writable only by the running user. On POSIX, partial-write paths use `O_NOFOLLOW` so a pre-placed symlink at the partial path is refused; on Windows the partial path is written without a symlink check (no `O_NOFOLLOW` equivalent without ctypes).

## Release integrity

We don't ship platform-native code-signing CA certs. Instead:

- **PyPI** — `pip install pdf-smasher` uses PyPI's checksum + GitHub OIDC trusted-publishing provenance.
- **Docker** — `ghcr.io/hank-ai/hankpdf@sha256:…` pins to immutable digests. Every pushed image is signed with cosign (keyless, via GitHub's OIDC issuer) and carries a SLSA v1 build-provenance attestation + SPDX SBOM. Verify via `cosign verify ghcr.io/hank-ai/hankpdf:<tag> --certificate-identity-regexp 'https://github\\.com/hank-ai/hankpdf/\\.github/workflows/docker\\.yml@refs/.+' --certificate-oidc-issuer https://token.actions.githubusercontent.com` and `gh attestation verify oci://ghcr.io/hank-ai/hankpdf:<tag> --owner hank-ai`.
- **Windows jbig2.exe bundle** — release assets include a SHA-256 sidecar (`jbig2-windows-x64.zip.sha256`) plus SLSA build-provenance attestation. The installer script refuses to install any download whose digest doesn't match the sidecar. The installer script itself is published as a release asset with its own `.sha256`, and users should install via the tagged URL (`releases/download/jbig2-windows-vX.Y.Z/install_jbig2_windows.ps1`) — never the mutable `raw/main/…` URL.
- **GitHub Releases** — SHA-256 checksums for every asset are published alongside the asset. Image digests appear in the Docker publish workflow summary.

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for the correlation-ID on-call recovery workflow that ties stderr lines back to structured reports without ever recording plaintext filenames.

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
