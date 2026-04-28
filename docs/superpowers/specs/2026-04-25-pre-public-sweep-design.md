# Pre-Public-Release Sweep — Design

**Date:** 2026-04-25
**Author:** Jack Neil (with audit assistance from parallel review agents)
**Goal:** Make `pdf-smasher` safe to flip from private to public on `github.com/hank-ai/pdf-smasher`, with no embarrassments, no placeholder URLs, no internal-handle leaks, and the highest-impact security gaps closed.

## 1. Context

This repo is currently private. Status: working CLI + library, 285+ test functions, multi-OS CI, GHCR image build, Windows installer. Wave 5 (commit `128e839`) shipped supply-chain hardening: SHA-pinned actions, digest-pinned base image, frozen `uv sync`, cosign signing, SLSA attestations, SBOM.

A four-pronged audit (secrets/PII, code vulns, supply chain, public-readiness) ran against the working tree and full git history. Findings:

- **No secrets, credentials, certs, or real PHI in the working tree or any commit.** Clean across `git log --all -p`.
- **Supply chain is unusually well-hardened.** `pip-audit` clean (0 CVEs, 41 deps). License tree clean (no GPL/AGPL/commercial). Workflows tightly scoped, no `pull_request_target`, all secrets gated.
- **No critical functional regressions.** Initial audit flagged 9 sites of `except A, B:` syntax as Python 2 holdovers — but Python 3.14 (the project's pinned floor) accepts unparenthesized `except` lists per PEP 758. All 340 tests collect and the 245 unit tests pass on 3.14. Code is fine. The pattern is unfamiliar but valid.
- **~10 placeholder/cleanup items** that need to be fixed before flipping public: stale `ourorg` URLs, fake security email, internal teammate handle in a doc heading, aspirational S3 bucket name, etc.
- **3 HIGH-severity security gaps** in code that affect what we ship: decompression-bomb gap on the compress path, `--password-file` flag is a no-op, default `max_input_mb=2000.0` admits 2 GB attacker-controlled inputs into RAM.
- **Several MEDIUM hardening items**, mostly defense-in-depth.
- **Missing public-repo polish:** no `CODE_OF_CONDUCT.md`, no issue/PR templates, no PyPI publish workflow, `pre-commit` ecosystem missing from dependabot.

This single-PR design fixes everything in one cohesive merge so the repo is publishable on completion.

## 2. Goals & Non-Goals

**Goals:**

1. No placeholder URLs, fake addresses, or internal handles in any committed file.
2. The 3 HIGH-severity security gaps closed.
3. Sensible defense-in-depth on remaining MEDIUM items.
4. Public-repo polish (CoC, issue templates, PyPI publish workflow, dependabot pre-commit).
5. Re-establish ground truth on test count + GHCR-publishing claim in README.

**Non-Goals:**

1. PyPI publish itself — we add the workflow but do **not** flip on Trusted Publisher / cut a release in this PR.
2. Branch protection / `CODEOWNERS` — those happen post-flip via repo settings, not via code.
3. Rewriting git history. The one teammate-handle leak is in a commit message body (`0a966fe`); we fix the file but leave the commit message. (Internal handle in a commit body is low-impact and force-push-rewrites are noisier than the leak.)
4. Filling the empty corpus manifest with real fixtures.
5. Any feature work outside the audit findings.

## 3. Workstreams

The PR is large but the workstreams are independent and review cleanly when grouped this way. Conceptual order; commits will follow this order so reviewers can step through:

### W1. Placeholder + handle cleanup (release-blocker)

| Change | Files |
|---|---|
| `ourorg/pdf-smasher` → `hank-ai/pdf-smasher` | `pyproject.toml:50-53`, `docs/ARCHITECTURE.md:287`, `docs/ROADMAP.md:441` |
| Drop `Email: security@TBD.example` line entirely; rely on GitHub Security Advisories (already listed as preferred) | `SECURITY.md:8` |
| Drop `(shartzog P0)` from heading; rename to `## 2. The correlation-ID recovery workflow` | `docs/TROUBLESHOOTING.md:25` |
| Add `.claude/` to gitignore (mirroring `.firecrawl/` rule) | `.gitignore` line 1 |
| Generic-ize corpus mirror story (see W2) | `tests/STRATEGY.md`, `CONTRIBUTING.md`, `tests/corpus/manifest.json` schema, `scripts/fetch_corpus.py` |

### W2. Storage-agnostic corpus mirror

Today's wording assumes an S3 bucket. Reality: the mirror could be S3, an HTTPS host, an `http.server` on a workstation, an NFS mount via `file://` — anything `urllib.request.urlopen` understands.

Concrete changes:

1. **Manifest schema:** rename optional field `s3_mirror` → `mirror_url`. Manifest is empty today (no entries to migrate) but `tests/STRATEGY.md` shows `s3_mirror` in the example block; update the example.
2. **`scripts/fetch_corpus.py`:** rename the dict lookup `fixture.get("s3_mirror")` → `fixture.get("mirror_url")`. Behavior is unchanged (it's still passed to `urllib.request.urlopen`).
3. **`tests/STRATEGY.md` lines 70 + 76:** rewrite from "Upload to our S3 corpus bucket (once provisioned)" to "Upload to a mirror you control. Anything `urllib` can fetch works — S3 via HTTPS, your own HTTPS host, even a `file://` path during local development. The mirror is optional; the upstream `url` is the fallback."
4. **`CONTRIBUTING.md:66`:** rewrite to match.

### W3. HIGH security fixes

**W3a. Decompression-bomb pre-check on compress path.**

`pdf_smasher/engine/image_export.py:208` already gates on `target_w * target_h > _MAX_BOMB_PIXELS` before calling `page.render`. The compress path in `pdf_smasher/engine/rasterize.py:34-44` does not. Lift the bomb-check into `rasterize_page` so both paths are protected.

Plan: introduce a shared helper module `pdf_smasher/engine/_render_safety.py` exporting `check_render_size(width_pt: float, height_pt: float, dpi: float) -> None` (raises `DecompressionBombError` if `target_w * target_h > _MAX_BOMB_PIXELS`). Both `rasterize.py` and `image_export.py` import it; the existing inline check in `image_export.py:208` is replaced by the call. `_MAX_BOMB_PIXELS` moves to `_render_safety.py` as the canonical home. The CLI maps `DecompressionBombError` to `EXIT_DECOMPRESSION_BOMB=16` (existing behavior).

Test: synthetic PDF with `MediaBox = [0 0 1000000 1000000]`, render at 300 DPI, assert `DecompressionBombError` raised before pdfium allocates the bitmap. Use `pikepdf` to construct the synthetic PDF from scratch in the test.

**W3b. Plumb `--password-file` through to `pikepdf.open`.**

`pdf_smasher/engine/triage.py:171` opens with no password. The CLI accepts `--password-file` and reads it (`cli/main.py:498-501`), but it never reaches `pikepdf.open`.

Plan (`options.password` already exists on `CompressOptions` per `init.py:261`):

1. Add a `password: str | None = None` parameter to `triage()` (engine/triage.py:171).
2. Pass `options.password` from `compress()` to `triage()`.
3. Apply password to every site that opens user-supplied PDF bytes:
   - `engine/triage.py:171` — `pikepdf.open(io.BytesIO(pdf_bytes), password=password or "")`
   - `init.py:847` (per-page split via `pikepdf.open`) — same.
   - `image_export.py:159` — `pypdfium2.PdfDocument` accepts a `password=` kwarg; pass it.
   - `engine/rasterize.py:34` — `pdfium.PdfDocument(pdf_bytes, password=...)` — same.
4. CLI password-file read (`cli/main.py:498-501`): use `encoding="utf-8"` and `.removesuffix("\n")` instead of locale-default + `.strip()` (M5 from audit).
5. Test: encrypted-PDF fixture (synthetic, generated in the test from pikepdf with `pdf.save(..., encryption=...)`), confirm wrong password → `EXIT_ENCRYPTED=10` (as today), correct password → successful triage.

**W3c. Tighten input-size and page-count defaults.**

Currently:
- `--max-input-mb` default `2000.0` (2 GB)
- `--max-pages` default `None` (unlimited)

A 50 KB PDF with `/Count 100000000` materializes the page tree. A 2 GB attacker file is `read_bytes()`-ed into memory before triage runs.

New defaults:
- `--max-input-mb` default `250.0` (250 MB)
- `--max-pages` default `10000`

Both remain user-overridable via the existing CLI flags. Document in README and CHANGELOG section. Bump exit refusal messages to mention the override flag so users hitting the new cap know how to opt back into the old behavior.

**Stat-before-read:** in `cli/main.py:984`, replace `args.input.read_bytes()` with a stat first. If `args.input.stat().st_size > options.max_input_mb * 1024 * 1024`, refuse with the existing exit code before reading any bytes. Today the read happens **before** triage's size check, defeating the cap.

### W4. MEDIUM hardening

**W4a. Depth-cap fail-closed in triage.**
`pdf_smasher/engine/triage.py` `_walk_dict_for_names` returns "no hits" past `max_depth=12`. Change: when `depth > max_depth`, raise `MaliciousPDFError("nested resource tree exceeds inspection depth; refusing")`. Bump max_depth to 64 (large enough for legitimate heavily-nested PDFs — heavy form trees, tagged accessibility — while still bounding recursion-bomb attempts). The plan tuned this from 32 → 64 during round-1 review for more headroom. Existing test for the JS-detection path needs an extension.

**W4b. `O_NOFOLLOW` in `_atomic_write_bytes`.**
`pdf_smasher/utils/atomic.py:24-38` — replace `tmp.write_bytes(data)` with `os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)` then `os.write` + `os.close`. Refuses to follow a pre-placed symlink at the partial-write path. Document in `SECURITY.md` that the output directory is assumed to be writable only by the running user.

Note: `O_NOFOLLOW` is POSIX-only. On Windows, the call falls back to today's `tmp.write_bytes`. Guard with `os.name != "nt"`.

**W4c. Native binary path absolutization.**
`pdf_smasher/engine/codecs/jbig2.py:56` and friends call `subprocess.run(["jbig2", ...])`, resolving via `PATH` every time. Plan: at first use, `shutil.which("jbig2")` once, cache the absolute path on a module-level singleton, refuse with a clear error if not found. Same for `tesseract` and `qpdf` (audit module). Subprocess calls remain `shell=False`, list-form — no command injection surface, this is just hardening against shim-attack.

**W4d. PIL `MAX_IMAGE_PIXELS` cap idempotent.**
`pdf_smasher/_pillow_hardening.py` runs as a side-effect of `import pdf_smasher`. Move the body into `ensure_capped()` and call it from each engine module that touches PIL (`rasterize.py`, `image_export.py`, `compose.py`, `verifier/*.py`). Idempotent — calling twice is a no-op. Programmatic callers that import only an engine submodule now get the cap installed.

**W4e. PyPI publish workflow.**
Add `.github/workflows/release.yml` that uses `pypa/gh-action-pypi-publish` with OIDC trusted publishing. Triggered on `release: published` (i.e., when a GitHub Release is cut). `permissions: id-token: write, contents: read`. Pinned by SHA. **Does NOT publish in this PR** — the workflow is dormant until the first GitHub Release is cut. SECURITY.md already advertises this; this PR makes the advertisement honest.

Configuring PyPI's Trusted Publisher side requires logging into PyPI and adding the publisher entry; that's a manual step Jack does once, separately from this PR. Workflow is in place ready for it.

**W4f. Dependabot for `pre-commit` ecosystem.**
Add a fourth ecosystem block to `.github/dependabot.yml` to keep `.pre-commit-config.yaml` `rev:` pins fresh.

### W5. Public-repo polish

**W5a. `CODE_OF_CONDUCT.md`.**
Add Contributor Covenant 2.1 (~3.5 KB), reporting channel: GitHub Security Advisories (matches W1's SECURITY.md update — keep one channel for both security and CoC reports).

**W5b. Issue templates + PR template.**
- `.github/ISSUE_TEMPLATE/bug_report.yml` — required fields: `hankpdf --version` output, OS, sample input behavior, attached correlation-id.
- `.github/ISSUE_TEMPLATE/feature_request.yml` — short.
- `.github/ISSUE_TEMPLATE/config.yml` — disable blank issues, point at Security Advisories for vulns.
- `.github/PULL_REQUEST_TEMPLATE.md` — checklist: ran `uv run pytest`, `uv run ruff check`, conventional-commit type, matched existing patterns. Reminder of CONTRIBUTING.md rules.

### W6. README/docs accuracy

**W6a. Test count.** Verified: 340 tests collected, 245 unit tests pass on Python 3.14. Update README:7 (status line) and README:248 (`pytest -q` comment) from "327 tests passing" to "340 tests passing".

**W6b. GHCR claim.** README:7 says "Not yet published to PyPI or GHCR — install from the repo." But `docker.yml` does push to GHCR on every `main` merge. Reconcile:
- If GHCR `:latest` already exists post-Wave-5 merges, drop the "or GHCR" half. Sanity check: `gh api /users/hank-ai/packages/container/hankpdf/versions` (need to confirm this works for an org's package).
- If GHCR is genuinely empty (workflow ran but failed silently, or the image visibility setting is private), either fix that or be explicit about why.

**W6c. Docker-image tag.** README:109 references `:v0.0.1` while `pyproject.toml` version is `0.0.0`. Pick one — recommendation: leave version `0.0.0` (we're not cutting a release in this PR), and update the README example to `ghcr.io/hank-ai/hankpdf:latest` to side-step the version-tag-doesn't-exist problem.

## 4. Test Strategy

Each W-section above has an associated test plan. Aggregate:

1. **Bomb-check test** (W3a): synthetic PDF, assert refusal before pdfium allocates.
2. **Encrypted-PDF password test** (W3b): correct + wrong password paths.
3. **Defaults test** (W3c): assert new defaults in argparse.
4. **Stat-before-read test** (W3c): file > cap → refuse before reading.
5. **Depth-cap test** (W4a): 33-deep nested dict → `MaliciousPDFError`.
6. **Atomic-write symlink test** (W4b): pre-placed symlink at partial path → refuse (POSIX only).

All tests live in `tests/unit/` matching existing naming. Run via existing CI matrix.

## 5. Documentation Updates

- `README.md` — test count, GHCR/PyPI status reconciled, docker-image tag (W6).
- `SECURITY.md` — drop fake email; document W4b output-dir assumption.
- `CONTRIBUTING.md` — corpus-mirror wording (W2).
- `tests/STRATEGY.md` — corpus-mirror wording + manifest field rename (W2).
- `docs/ARCHITECTURE.md`, `docs/ROADMAP.md` — `ourorg` → `hank-ai/pdf-smasher` (W1).
- `docs/TROUBLESHOOTING.md` — drop teammate handle (W1).
- New: `CODE_OF_CONDUCT.md` (W5a).

## 6. Out of Scope

- Filling `tests/corpus/manifest.json` with real fixtures.
- Cutting a PyPI / GHCR release.
- Branch protection / CODEOWNERS.
- Force-pushing to rewrite the `(shartzog P0)` mention in commit `0a966fe`'s body.
- Migrating `docs/superpowers/plans/` out of `docs/`.

These are deferred. None block flipping public.

## 7. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| `--max-input-mb` default change (2000→250) breaks an existing CLI user with a 1 GB workflow | The flag still exists; user runs `--max-input-mb 1000`. README + CHANGELOG note the breaking change. Repo is pre-1.0, no SemVer guarantee. |
| `--max-pages` default change (None→10000) breaks a 100k-page-PDF workflow | Same. Flag override available. Anyone routinely processing 10k+ page PDFs is unusual enough to read the changelog. |
| `O_NOFOLLOW` change breaks Windows | Guarded behind `os.name != "nt"`. Test path runs only on POSIX. |
| Adding `.claude/` to `.gitignore` masks an already-tracked file | `git ls-files .claude/` returns empty (verified). Safe. |
| Subagents introduce a regression while threading password through 4 PDF-open sites | Each task ends with `uv run pytest` green; password test asserts both correct + wrong paths; baseline 245 unit tests stay green. |
| PyPI publish workflow accidentally publishes from this PR | Workflow trigger is `release: published` only. No release is cut in this PR. |

## 8. Open Decisions (resolved during brainstorm)

- ✅ **Single PR vs split:** single PR (user choice).
- ✅ **Security email:** drop the line entirely; GHSA only.
- ✅ **Corpus mirror wording:** generic ("any URL `urllib` can fetch").
- ✅ **`--max-input-mb` default:** 250.0 (was 2000.0).
- ✅ **`--max-pages` default:** 10000 (was `None`).
- ✅ **PyPI publish workflow:** add but do NOT publish in this PR.
- ✅ **`CODE_OF_CONDUCT.md`:** add (Contributor Covenant 2.1).
- ✅ **Issue/PR templates:** add.
- ✅ **`pyproject.toml` author email:** leave empty (no signal user wants to add it).
- ✅ **Commit message rewrite for `0a966fe`:** no — file fix is enough.

## 9. Acceptance Criteria

The PR is ready to merge when ALL of the following are true:

1. `uv run pytest` passes (baseline + new tests). CI proves it.
2. `grep -r "ourorg" .` returns no matches outside `.git/` and audit-history files.
3. `grep -ri "shartzog\|TBD.example\|our-corpus-bucket" .` returns no matches outside `.git/` and `docs/superpowers/specs/`.
4. Synthetic 1M×1M-point PDF raises `DecompressionBombError` from `rasterize_page` before pdfium allocates (test asserts).
5. `--password-file` with a correct password successfully triages an encrypted fixture (test asserts).
6. Default `--max-input-mb` is `250.0`, default `--max-pages` is `10000` (asserted in a test).
7. `.github/workflows/release.yml` exists, lints clean via `actionlint`, and is referenced from SECURITY.md as the publish path.
8. `CODE_OF_CONDUCT.md` exists at repo root.
9. `.github/ISSUE_TEMPLATE/{bug_report.yml,feature_request.yml,config.yml}` and `.github/PULL_REQUEST_TEMPLATE.md` exist.
10. `.gitignore` contains `.claude/`.
11. `.github/dependabot.yml` includes the `pre-commit` ecosystem.
12. README test count and GHCR claim match reality.
13. `/dc` post-implementation review passes clean (no CRITICAL or MEDIUM findings) — Phase 5 of /jack-it-up.
