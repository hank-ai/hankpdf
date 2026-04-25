# Pre-Public-Release Sweep — Design

**Date:** 2026-04-25
**Author:** Jack Neil (with audit assistance from parallel review agents)
**Goal:** Make `pdf-smasher` safe to flip from private to public on `github.com/hank-ai/hankpdf`, with no embarrassments, no exploitable vulnerabilities, and no broken-on-import code.

## 1. Context

This repo is currently private. Status: working CLI + library, 285+ test functions, multi-OS CI, GHCR image build, Windows installer. Wave 5 (commit `128e839`) shipped supply-chain hardening: SHA-pinned actions, digest-pinned base image, frozen `uv sync`, cosign signing, SLSA attestations, SBOM.

A four-pronged audit (secrets/PII, code vulns, supply chain, public-readiness) ran against the working tree and full git history. Findings:

- **No secrets, credentials, certs, or real PHI in the working tree or any commit.** Clean across `git log --all -p`.
- **Supply chain is unusually well-hardened.** `pip-audit` clean (0 CVEs, 41 deps). License tree clean (no GPL/AGPL/commercial). Workflows tightly scoped, no `pull_request_target`, all secrets gated.
- **One critical functional regression:** Wave 5 introduced 9 sites of Python 2 `except A, B:` syntax. The package fails to import on Python 3. CI is silently passing because the lint/typecheck jobs don't actually `import pdf_smasher` — only ruff/mypy parse it (and ruff/mypy tolerate the syntax through their own AST). This is the single biggest finding.
- **~10 placeholder/cleanup items** that need to be fixed before flipping public: stale `ourorg` URLs, fake security email, internal teammate handle in a doc heading, aspirational S3 bucket name, etc.
- **3 HIGH-severity security gaps** in code that affect what we ship: decompression-bomb gap on the compress path, `--password-file` flag is a no-op, default `max_input_mb=2000.0` admits 2 GB attacker-controlled inputs into RAM.
- **Several MEDIUM hardening items**, mostly defense-in-depth.
- **Missing public-repo polish:** no `CODE_OF_CONDUCT.md`, no issue/PR templates, no PyPI publish workflow, `pre-commit` ecosystem missing from dependabot.

This single-PR design fixes everything in one cohesive merge so the repo is publishable on completion.

## 2. Goals & Non-Goals

**Goals:**

1. Package imports cleanly on Python 3.14 (CRITICAL).
2. CI gate prevents this regression class from reoccurring.
3. No placeholder URLs, fake addresses, or internal handles in any committed file.
4. The 3 HIGH-severity security gaps closed.
5. Sensible defense-in-depth on remaining MEDIUM items.
6. Public-repo polish (CoC, issue templates, PyPI publish workflow, dependabot pre-commit).
7. Re-establish ground truth on test count + GHCR-publishing claim in README.

**Non-Goals:**

1. PyPI publish itself — we add the workflow but do **not** flip on Trusted Publisher / cut a release in this PR.
2. Branch protection / `CODEOWNERS` — those happen post-flip via repo settings, not via code.
3. Rewriting git history. The one teammate-handle leak is in a commit message body (`0a966fe`); we fix the file but leave the commit message. (Internal handle in a commit body is low-impact and force-push-rewrites are noisier than the leak.)
4. Filling the empty corpus manifest with real fixtures.
5. Any feature work outside the audit findings.

## 3. Workstreams

The PR is large but the workstreams are independent and review cleanly when grouped this way. Conceptual order; commits will follow this order so reviewers can step through:

### W1. Fix the import (CRITICAL, must come first)

Parenthesize all 9 Python 2 `except A, B:` sites:

| File | Line |
|---|---|
| `pdf_smasher/audit.py` | 44 |
| `pdf_smasher/_version.py` | 61 |
| `pdf_smasher/__init__.py` | 1045, 1104 |
| `pdf_smasher/cli/main.py` | 574 |
| `pdf_smasher/engine/compose.py` | 78, 134 |
| `pdf_smasher/engine/ocr.py` | 109 |
| `pdf_smasher/engine/triage.py` | 111 |

**Add a CI smoke step** at the top of `ci.yml`'s test job (and to the lint job for early failure): `python -c "import pdf_smasher"`. Cheap, immediate, prevents recurrence. This is the single most important new test in the PR.

After the fix, run `uv run pytest --collect-only -q` to get the real test count and use it in W7.

### W2. Placeholder + handle cleanup (release-blocker)

| Change | Files |
|---|---|
| `ourorg/pdf-smasher` → `hank-ai/hankpdf` | `pyproject.toml:50-53`, `docs/ARCHITECTURE.md:287`, `docs/ROADMAP.md:441` |
| Drop `Email: security@TBD.example` line entirely; rely on GitHub Security Advisories (already listed as preferred) | `SECURITY.md:8` |
| Drop `(shartzog P0)` from heading; rename to `## 2. The correlation-ID recovery workflow` | `docs/TROUBLESHOOTING.md:25` |
| Add `.claude/` to gitignore (mirroring `.firecrawl/` rule) | `.gitignore` line 1 |
| Generic-ize corpus mirror story (see W3) | `tests/STRATEGY.md`, `CONTRIBUTING.md`, `tests/corpus/manifest.json` schema, `scripts/fetch_corpus.py` |

### W3. Storage-agnostic corpus mirror

Today's wording assumes an S3 bucket. Reality: the mirror could be S3, an HTTPS host, an `http.server` on a workstation, an NFS mount via `file://` — anything `urllib.request.urlopen` understands.

Concrete changes:

1. **Manifest schema:** rename optional field `s3_mirror` → `mirror_url`. Manifest is empty today (no entries to migrate) but `tests/STRATEGY.md` shows `s3_mirror` in the example block; update the example.
2. **`scripts/fetch_corpus.py`:** rename the dict lookup `fixture.get("s3_mirror")` → `fixture.get("mirror_url")`. Behavior is unchanged (it's still passed to `urllib.request.urlopen`).
3. **`tests/STRATEGY.md` lines 70 + 76:** rewrite from "Upload to our S3 corpus bucket (once provisioned)" to "Upload to a mirror you control. Anything `urllib` can fetch works — S3 via HTTPS, your own HTTPS host, even a `file://` path during local development. The mirror is optional; the upstream `url` is the fallback."
4. **`CONTRIBUTING.md:66`:** rewrite to match.

### W4. HIGH security fixes

**W4a. Decompression-bomb pre-check on compress path.**

`pdf_smasher/engine/image_export.py:208` already gates on `target_w * target_h > _MAX_BOMB_PIXELS` before calling `page.render`. The compress path in `pdf_smasher/engine/rasterize.py:34-44` does not. Lift the bomb-check into `rasterize_page` so both paths are protected.

Plan: introduce a shared helper module `pdf_smasher/engine/_render_safety.py` exporting `check_render_size(width_pt: float, height_pt: float, dpi: float) -> None` (raises `DecompressionBombError` if `target_w * target_h > _MAX_BOMB_PIXELS`). Both `rasterize.py` and `image_export.py` import it; the existing inline check in `image_export.py:208` is replaced by the call. `_MAX_BOMB_PIXELS` moves to `_render_safety.py` as the canonical home. The CLI maps `DecompressionBombError` to `EXIT_DECOMPRESSION_BOMB=16` (existing behavior).

Test: synthetic PDF with `MediaBox = [0 0 1000000 1000000]`, render at 300 DPI, assert `DecompressionBombError` raised before pdfium allocates the bitmap. Use `pikepdf` to construct the synthetic PDF from scratch in the test.

**W4b. Plumb `--password-file` through to `pikepdf.open`.**

`pdf_smasher/engine/triage.py:171` opens with no password. The CLI accepts `--password-file` and reads it (`cli/main.py:498-501`), but it never reaches `pikepdf.open`.

Plan (`options.password` already exists on `CompressOptions` per `__init__.py:261`):

1. Add a `password: str | None = None` parameter to `triage()` (engine/triage.py:171).
2. Pass `options.password` from `compress()` to `triage()`.
3. Apply password to every site that opens user-supplied PDF bytes:
   - `engine/triage.py:171` — `pikepdf.open(io.BytesIO(pdf_bytes), password=password or "")`
   - `__init__.py:847` (per-page split via `pikepdf.open`) — same.
   - `image_export.py:159` — `pypdfium2.PdfDocument` accepts a `password=` kwarg; pass it.
   - `engine/rasterize.py:34` — `pdfium.PdfDocument(pdf_bytes, password=...)` — same.
4. CLI password-file read (`cli/main.py:498-501`): use `encoding="utf-8"` and `.removesuffix("\n")` instead of locale-default + `.strip()` (M5 from audit).
5. Test: encrypted-PDF fixture (synthetic, generated in the test from pikepdf with `pdf.save(..., encryption=...)`), confirm wrong password → `EXIT_ENCRYPTED=10` (as today), correct password → successful triage.

**W4c. Tighten input-size and page-count defaults.**

Currently:
- `--max-input-mb` default `2000.0` (2 GB)
- `--max-pages` default `None` (unlimited)

A 50 KB PDF with `/Count 100000000` materializes the page tree. A 2 GB attacker file is `read_bytes()`-ed into memory before triage runs.

New defaults:
- `--max-input-mb` default `250.0` (250 MB)
- `--max-pages` default `10000`

Both remain user-overridable via the existing CLI flags. Document in README and CHANGELOG section. Bump exit refusal messages to mention the override flag so users hitting the new cap know how to opt back into the old behavior.

**Stat-before-read:** in `cli/main.py:984`, replace `args.input.read_bytes()` with a stat first. If `args.input.stat().st_size > options.max_input_mb * 1024 * 1024`, refuse with the existing exit code before reading any bytes. Today the read happens **before** triage's size check, defeating the cap.

### W5. MEDIUM hardening

**W5a. Depth-cap fail-closed in triage.**
`pdf_smasher/engine/triage.py` `_walk_dict_for_names` returns "no hits" past `max_depth=12`. Change: when `depth > max_depth`, raise `MaliciousPDFError("nested resource tree exceeds inspection depth; refusing")`. Bump max_depth to 32 (large enough for any legitimate PDF; deep-nesting attacks are now refused loudly instead of silently waved through). Existing test for the JS-detection path needs an extension.

**W5b. `O_NOFOLLOW` in `_atomic_write_bytes`.**
`pdf_smasher/utils/atomic.py:24-38` — replace `tmp.write_bytes(data)` with `os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)` then `os.write` + `os.close`. Refuses to follow a pre-placed symlink at the partial-write path. Document in `SECURITY.md` that the output directory is assumed to be writable only by the running user.

Note: `O_NOFOLLOW` is POSIX-only. On Windows, the call falls back to today's `tmp.write_bytes`. Guard with `os.name != "nt"`.

**W5c. Native binary path absolutization.**
`pdf_smasher/engine/codecs/jbig2.py:56` and friends call `subprocess.run(["jbig2", ...])`, resolving via `PATH` every time. Plan: at first use, `shutil.which("jbig2")` once, cache the absolute path on a module-level singleton, refuse with a clear error if not found. Same for `tesseract` and `qpdf` (audit module). Subprocess calls remain `shell=False`, list-form — no command injection surface, this is just hardening against shim-attack.

**W5d. PIL `MAX_IMAGE_PIXELS` cap idempotent.**
`pdf_smasher/_pillow_hardening.py` runs as a side-effect of `import pdf_smasher`. Move the body into `ensure_capped()` and call it from each engine module that touches PIL (`rasterize.py`, `image_export.py`, `compose.py`, `verifier/*.py`). Idempotent — calling twice is a no-op. Programmatic callers that import only an engine submodule now get the cap installed.

**W5e. PyPI publish workflow.**
Add `.github/workflows/release.yml` that uses `pypa/gh-action-pypi-publish` with OIDC trusted publishing. Triggered on `release: published` (i.e., when a GitHub Release is cut). `permissions: id-token: write, contents: read`. Pinned by SHA. **Does NOT publish in this PR** — the workflow is dormant until the first GitHub Release is cut. SECURITY.md already advertises this; this PR makes the advertisement honest.

Configuring PyPI's Trusted Publisher side requires logging into PyPI and adding the publisher entry; that's a manual step Jack does once, separately from this PR. Workflow is in place ready for it.

**W5f. Dependabot for `pre-commit` ecosystem.**
Add a fourth ecosystem block to `.github/dependabot.yml` to keep `.pre-commit-config.yaml` `rev:` pins fresh.

### W6. Public-repo polish

**W6a. `CODE_OF_CONDUCT.md`.**
Add Contributor Covenant 2.1 (~3.5 KB), reporting channel: GitHub Security Advisories (matches W2's SECURITY.md update — keep one channel for both security and CoC reports).

**W6b. Issue templates + PR template.**
- `.github/ISSUE_TEMPLATE/bug_report.yml` — required fields: `hankpdf --version` output, OS, sample input behavior, attached correlation-id.
- `.github/ISSUE_TEMPLATE/feature_request.yml` — short.
- `.github/ISSUE_TEMPLATE/config.yml` — disable blank issues, point at Security Advisories for vulns.
- `.github/PULL_REQUEST_TEMPLATE.md` — checklist: ran `uv run pytest`, `uv run ruff check`, conventional-commit type, matched existing patterns. Reminder of CONTRIBUTING.md rules.

### W7. README/docs accuracy

**W7a. Test count.** After W1 lands, run `uv run pytest --collect-only -q` and update README:7 (status line) and README:248 (`pytest -q` comment) to the real number.

**W7b. GHCR claim.** README:7 says "Not yet published to PyPI or GHCR — install from the repo." But `docker.yml` does push to GHCR on every `main` merge. Reconcile:
- If GHCR `:latest` already exists post-Wave-5 merges, drop the "or GHCR" half. Sanity check: `gh api /users/hank-ai/packages/container/hankpdf/versions` (need to confirm this works for an org's package).
- If GHCR is genuinely empty (workflow ran but failed silently, or the image visibility setting is private), either fix that or be explicit about why.

**W7c. Docker-image tag.** README:109 references `:v0.0.1` while `pyproject.toml` version is `0.0.0`. Pick one — recommendation: leave version `0.0.0` (we're not cutting a release in this PR), and update the README example to `ghcr.io/hank-ai/hankpdf:latest` to side-step the version-tag-doesn't-exist problem.

## 4. Test Strategy

Each W-section above has an associated test plan. Aggregate:

1. **Smoke import test** (W1): `python -c "import pdf_smasher"` in CI. Catches regression of the W1 issue.
2. **Bomb-check test** (W4a): synthetic PDF, assert refusal before pdfium allocates.
3. **Encrypted-PDF password test** (W4b): correct + wrong password paths.
4. **Defaults test** (W4c): assert new defaults in argparse.
5. **Stat-before-read test** (W4c): file > cap → refuse before reading.
6. **Depth-cap test** (W5a): 33-deep nested dict → `MaliciousPDFError`.
7. **Atomic-write symlink test** (W5b): pre-placed symlink at partial path → refuse (POSIX only).

All tests live in `tests/unit/` matching existing naming. Run via existing CI matrix.

## 5. Documentation Updates

- `README.md` — test count, GHCR/PyPI status reconciled, docker-image tag (W7).
- `SECURITY.md` — drop fake email; document W5b output-dir assumption.
- `CONTRIBUTING.md` — corpus-mirror wording (W3).
- `tests/STRATEGY.md` — corpus-mirror wording + manifest field rename (W3).
- `docs/ARCHITECTURE.md`, `docs/ROADMAP.md` — `ourorg` → `hank-ai/hankpdf` (W2).
- `docs/TROUBLESHOOTING.md` — drop teammate handle (W2).
- New: `CODE_OF_CONDUCT.md` (W6a).

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
| Subagent execution introduces a new bug while fixing 9 syntax sites | Each subagent task is small + has a verification step (`python -c "import pdf_smasher"`). |
| Adding `.claude/` to `.gitignore` masks an already-tracked file | `git ls-files .claude/` returns empty (verified). Safe. |
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

1. `python3 -c "import pdf_smasher"` succeeds. CI proves it.
2. `uv run pytest` passes. CI proves it.
3. `grep -r "ourorg" .` returns no matches outside `.git/` and audit-history files.
4. `grep -ri "shartzog\|TBD.example\|our-corpus-bucket" .` returns no matches outside `.git/` and `docs/superpowers/specs/`.
5. `python -c "from pdf_smasher.engine.rasterize import rasterize_page"` followed by feeding a synthetic 1M×1M-point PDF raises `DecompressionBombError` (test asserts).
6. `--password-file` with a correct password successfully triages an encrypted fixture (test asserts).
7. Default `--max-input-mb` is `250.0`, default `--max-pages` is `10000` (asserted in a test).
8. `.github/workflows/release.yml` exists, lints clean via `actionlint`, and is referenced from SECURITY.md as the publish path.
9. `CODE_OF_CONDUCT.md` exists at repo root.
10. `.github/ISSUE_TEMPLATE/{bug_report.yml,feature_request.yml,config.yml}` and `.github/PULL_REQUEST_TEMPLATE.md` exist.
11. `.gitignore` contains `.claude/`.
12. `.github/dependabot.yml` includes the `pre-commit` ecosystem.
13. README test count and GHCR claim match reality.
14. `/dc` post-implementation review passes clean (no CRITICAL or MEDIUM findings) — Phase 5 of /jack-it-up.
