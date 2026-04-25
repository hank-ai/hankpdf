# Pre-Public-Release Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land all release-blocker cleanup, security hardening, and public-repo polish from `docs/superpowers/specs/2026-04-25-pre-public-sweep-design.md` in a single PR so `pdf-smasher` is safe to flip public.

**Architecture:** Mechanical doc + URL cleanup, plus surgical engine-level hardening (shared render-safety helper, password threading through PDF-open call sites, tighter input-size defaults, depth-cap fail-closed in triage, `O_NOFOLLOW` on partial-write paths, idempotent Pillow cap, absolutized native-binary paths). Plus `release.yml` (dormant), CoC, issue/PR templates, dependabot pre-commit ecosystem, README accuracy.

**Tech Stack:** Python 3.14, `pikepdf`, `pypdfium2`, `Pillow`, `pytest`, `uv`, GitHub Actions.

**Pre-flight (run once before Task 1):**

```bash
uv run pytest tests/unit -q
```

Expect 245 passed. This is the green baseline.

---

## File Structure

**Create:**
- `pdf_smasher/engine/_render_safety.py` — shared `check_render_size()`
- `tests/unit/engine/__init__.py` (only if missing)
- `tests/unit/engine/test_render_safety.py`
- `tests/unit/engine/test_password_plumbing.py`
- `tests/unit/test_atomic_nofollow.py`
- `tests/unit/engine/test_triage_depth_cap.py`
- `tests/unit/test_input_size_limits.py`
- `tests/unit/test_pillow_cap_idempotent.py`
- `.github/workflows/release.yml`
- `.github/ISSUE_TEMPLATE/bug_report.yml`
- `.github/ISSUE_TEMPLATE/feature_request.yml`
- `.github/ISSUE_TEMPLATE/config.yml`
- `.github/PULL_REQUEST_TEMPLATE.md`
- `CODE_OF_CONDUCT.md`
- `CHANGELOG.md`

**Modify:**
- `pyproject.toml`, `docs/ARCHITECTURE.md` (URL fix + render-safety section), `docs/ROADMAP.md`, `docs/TROUBLESHOOTING.md`, `SECURITY.md`, `CONTRIBUTING.md`, `README.md`, `.gitignore`, `.github/dependabot.yml`
- `tests/STRATEGY.md`, `scripts/fetch_corpus.py`
- `tests/unit/test_types.py` (assert tightened defaults)
- `pdf_smasher/types.py` (defaults; `max_pages: int | None = 10000`)
- `pdf_smasher/cli/main.py` (defaults, password file read, stat-before-read in `else:` branch)
- `pdf_smasher/__init__.py` (password threading into public `triage` shim, `compress` triage call, page-sizing pdfium open at line 834, per-page split; enriched refusal messages)
- `pdf_smasher/engine/triage.py` (password kwarg; `is_encrypted` reflects real state on success path; depth-cap fail-closed at 64)
- `pdf_smasher/engine/canonical.py` (password kwarg on `canonical_input_sha256`)
- `pdf_smasher/engine/rasterize.py` (use `_render_safety`; add `password` kwarg, used by both compress route — pass `None` — and image-export route — forward user password)
- `pdf_smasher/engine/image_export.py` (use `_render_safety`; keep `_MAX_BOMB_PIXELS` alias; password kwarg on user-input pdfium open)
- `pdf_smasher/utils/atomic.py` (`O_NOFOLLOW` on POSIX)
- `pdf_smasher/_pillow_hardening.py` (idempotent `ensure_capped()`)
- `pdf_smasher/engine/{rasterize,image_export,compose,verifier,background,foreground,mask,ocr,strategy}.py` (call `ensure_capped()` after PIL import)
- `pdf_smasher/engine/codecs/jbig2.py`, `pdf_smasher/audit.py` (absolute binary paths via cached `shutil.which`)

---

## Task 1: Placeholder + handle cleanup

**Files:**
- Modify: `pyproject.toml:50-53`
- Modify: `docs/ARCHITECTURE.md:287`
- Modify: `docs/ROADMAP.md:441`
- Modify: `SECURITY.md:8`
- Modify: `docs/TROUBLESHOOTING.md:25`
- Modify: `.gitignore` (add `.claude/`)

- [ ] **Step 1.1: Replace `ourorg/pdf-smasher` with `hank-ai/hankpdf` in `pyproject.toml`**

In `pyproject.toml`, change the `[project.urls]` block:

```toml
[project.urls]
Homepage = "https://github.com/hank-ai/hankpdf"
Documentation = "https://github.com/hank-ai/hankpdf/tree/main/docs"
Repository = "https://github.com/hank-ai/hankpdf"
Issues = "https://github.com/hank-ai/hankpdf/issues"
```

- [ ] **Step 1.2: Same replacement in `docs/ARCHITECTURE.md:287`**

Change `ghcr.io/ourorg/pdf-smasher:X.Y` → `ghcr.io/hank-ai/hankpdf:X.Y`.

- [ ] **Step 1.3: Same replacement in `docs/ROADMAP.md:441`**

Change `ghcr.io/ourorg/pdf-smasher:X.Y` → `ghcr.io/hank-ai/hankpdf:X.Y`.

- [ ] **Step 1.4: Drop fake email line in `SECURITY.md`**

Find the line `- Email: security@TBD.example` (around line 8) and delete it. Leave the GitHub Security Advisories bullet above it as the sole reporting channel.

- [ ] **Step 1.5: Drop teammate handle in `docs/TROUBLESHOOTING.md:25`**

Change the heading `## 2. The correlation-ID recovery workflow (shartzog P0)` to `## 2. The correlation-ID recovery workflow`.

- [ ] **Step 1.6: Add `.claude/` to `.gitignore`**

In `.gitignore`, add `.claude/` directly under the existing `.firecrawl/` line.

- [ ] **Step 1.7: Verify**

```bash
grep -rn "ourorg\|TBD.example\|shartzog" \
  --include="*.md" --include="*.toml" --include="*.py" --include="*.yml" \
  --exclude-dir=.claude --exclude-dir=.git --exclude-dir=docs/superpowers/specs .
```

Expected output: no matches. (The spec file under `docs/superpowers/specs/` and any local-only `.claude/` checkpoint files reference these strings; both are excluded.)

```bash
grep -E "^\.claude/" .gitignore
```

Expected: `.claude/` line found.

```bash
uv run pytest tests/unit -q
```

Expected: 245 passed.

- [ ] **Step 1.8: Commit**

```bash
git add pyproject.toml docs/ARCHITECTURE.md docs/ROADMAP.md SECURITY.md docs/TROUBLESHOOTING.md .gitignore
git commit -m "chore(public-prep): replace ourorg URLs, drop placeholder email and handle, gitignore .claude/"
```

---

## Task 2: Storage-agnostic corpus mirror

**Files:**
- Modify: `tests/STRATEGY.md`
- Modify: `CONTRIBUTING.md:66`
- Modify: `scripts/fetch_corpus.py`

- [ ] **Step 2.1: Rewrite `tests/STRATEGY.md` corpus block**

Find the section starting around line 60 ("How to add a corpus fixture"). Replace the third bullet ("Upload to our S3 corpus bucket (once provisioned).") with:

```
3. Upload to a mirror you control. Anything `urllib` can fetch works — S3 via HTTPS, your own HTTPS host, even a `file://` path during local development. The mirror is optional; the upstream `url` is the fallback.
```

In the example JSON block (around line 70), rename the field `s3_mirror` → `mirror_url` and change the example value from `s3://our-corpus-bucket/...` to `https://example.com/mirror/govinfo-2021-house-hearing.pdf`.

- [ ] **Step 2.2: Rewrite `CONTRIBUTING.md:66`**

Replace the line `Real PDFs are not committed. They live in an S3 bucket and are fetched on demand by ...` with:

```
Real PDFs are not committed. They live wherever you like (S3, an HTTPS host, a local cache) and are fetched on demand by `scripts/fetch_corpus.py` against `tests/corpus/manifest.json`.
```

- [ ] **Step 2.3: Rename field in `scripts/fetch_corpus.py`**

Change `fixture.get("s3_mirror") or fixture["url"]` to `fixture.get("mirror_url") or fixture["url"]`.

- [ ] **Step 2.4: Verify**

```bash
grep -rn "s3_mirror\|our-corpus-bucket" --include="*.py" --include="*.md" --include="*.json" .
```

Expected: no matches outside `docs/superpowers/specs/` and `.git/`.

```bash
uv run pytest tests/unit -q
```

Expected: 245 passed.

- [ ] **Step 2.5: Commit**

```bash
git add tests/STRATEGY.md CONTRIBUTING.md scripts/fetch_corpus.py
git commit -m "chore(corpus): rename s3_mirror→mirror_url, document storage-agnostic mirroring"
```

---

## Task 3: Shared render-safety helper

**Files:**
- Create: `pdf_smasher/engine/_render_safety.py`
- Create: `tests/unit/engine/test_render_safety.py`
- Modify: `pdf_smasher/engine/rasterize.py:34-44`
- Modify: `pdf_smasher/engine/image_export.py` (replace inline check around line 208)

- [ ] **Step 3.1: Make sure `tests/unit/engine/__init__.py` exists**

Run:

```bash
ls tests/unit/engine/__init__.py
```

If absent, create an empty file:

```bash
touch tests/unit/engine/__init__.py
```

- [ ] **Step 3.1b: Write the failing tests**

Create `tests/unit/engine/test_render_safety.py`:

```python
"""Render-size cap shared by rasterize.py and image_export.py."""

from __future__ import annotations

import pytest

from pdf_smasher import DecompressionBombError
from pdf_smasher.engine._render_safety import check_render_size


def test_normal_letter_size_at_300_dpi_passes() -> None:
    # 8.5x11 inches at 300 DPI = 2550x3300 = ~8.4 Mpx, well under cap
    check_render_size(width_pt=612.0, height_pt=792.0, dpi=300.0)


def test_huge_mediabox_at_300_dpi_refuses() -> None:
    with pytest.raises(DecompressionBombError):
        check_render_size(width_pt=1_000_000.0, height_pt=1_000_000.0, dpi=300.0)


def test_modest_dpi_on_small_page_passes() -> None:
    check_render_size(width_pt=612.0, height_pt=792.0, dpi=72.0)


def test_zero_or_negative_dimensions_refuse_with_value_error() -> None:
    with pytest.raises(ValueError):
        check_render_size(width_pt=0.0, height_pt=792.0, dpi=300.0)
    with pytest.raises(ValueError):
        check_render_size(width_pt=-1.0, height_pt=792.0, dpi=300.0)


def test_max_pixels_override_lets_callers_opt_in_to_higher_cap() -> None:
    # Default cap refuses 30000x30000 = 900 Mpx, but caller passes 2 Gpx ceiling.
    check_render_size(
        width_pt=72.0 * 1000.0,
        height_pt=72.0 * 1000.0,
        dpi=30.0,
        max_pixels=2_000_000_000,
    )
```

- [ ] **Step 3.2: Run test, see it fail**

```bash
uv run pytest tests/unit/engine/test_render_safety.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'pdf_smasher.engine._render_safety'`.

- [ ] **Step 3.3: Implement the helper**

Create `pdf_smasher/engine/_render_safety.py`:

```python
"""Shared pre-allocation render-size cap.

Both compress (rasterize.py) and image-export (image_export.py) paths
must check that a page's pixel count fits before asking pdfium to
allocate the bitmap. Without this check on the compress path, a PDF
with an oversized MediaBox triggers a multi-GB allocation inside
pdfium before any of our own guards fire.

Canonical home of the cap is ``pdf_smasher._limits.MAX_BOMB_PIXELS`` —
this module imports it and exposes ``check_render_size`` plus an opt-in
``max_pixels`` override for callers that knowingly want a higher ceiling
(e.g., a future ``render-page`` CLI dealing with engineering drawings).
"""

from __future__ import annotations

from pdf_smasher._limits import MAX_BOMB_PIXELS
from pdf_smasher.exceptions import DecompressionBombError

_POINTS_PER_INCH: float = 72.0


def check_render_size(
    width_pt: float,
    height_pt: float,
    dpi: float,
    *,
    max_pixels: int = MAX_BOMB_PIXELS,
) -> None:
    """Refuse if rasterizing the page at ``dpi`` would exceed ``max_pixels``.

    ``DecompressionBombError`` is raised before any allocation happens.
    The CLI maps the exception to ``EXIT_DECOMPRESSION_BOMB=16``.

    Pass ``max_pixels`` higher than the default only when the caller has
    bounded the allocation by some other means (rare).
    """
    if width_pt <= 0 or height_pt <= 0:
        raise ValueError(
            f"invalid page size: width_pt={width_pt!r}, height_pt={height_pt!r}; "
            "non-positive values often indicate a locked/encrypted pdfium handle "
            "fell back to a stub document"
        )
    target_w = round(width_pt * dpi / _POINTS_PER_INCH)
    target_h = round(height_pt * dpi / _POINTS_PER_INCH)
    if target_w * target_h > max_pixels:
        raise DecompressionBombError(
            f"page would render to {target_w}x{target_h} pixels "
            f"({target_w * target_h:,} px), exceeding cap of {max_pixels:,}"
        )
```

- [ ] **Step 3.4: Run test, see it pass**

```bash
uv run pytest tests/unit/engine/test_render_safety.py -v
```

Expected: 5 passed.

- [ ] **Step 3.5: Wire helper into `rasterize.py`**

In `pdf_smasher/engine/rasterize.py`, after the `page = pdf[page_index]` line and before `bitmap = page.render(...)`, add:

```python
from pdf_smasher.engine._render_safety import check_render_size  # at top of file

# inside the function, before page.render:
check_render_size(width_pt=width_pt, height_pt=height_pt, dpi=dpi)
```

Use the existing `width_pt, height_pt = page.get_size()` and `dpi` parameter already in scope.

- [ ] **Step 3.6: Wire helper into `image_export.py` (keep the `_MAX_BOMB_PIXELS` alias)**

In `pdf_smasher/engine/image_export.py`:

1. Add `from pdf_smasher.engine._render_safety import check_render_size` at the top.
2. Find the existing inline check (around line 208) that compares `target_w * target_h > _MAX_BOMB_PIXELS`. Replace just the `if … > _MAX_BOMB_PIXELS: raise DecompressionBombError(...)` block with a call to `check_render_size(width_pt=..., height_pt=..., dpi=...)` using the same inputs.
3. **Keep** the existing `_MAX_BOMB_PIXELS = MAX_BOMB_PIXELS` (or equivalent) module-level alias. `tests/unit/test_pillow_hardening.py` imports `_MAX_BOMB_PIXELS` from `image_export` and asserts it equals `PIL.Image.MAX_IMAGE_PIXELS`; removing it breaks that test.

- [ ] **Step 3.7: Run baseline + new tests**

```bash
uv run pytest tests/unit -q
```

Expected: all green (was 245 + 4 new = 249).

- [ ] **Step 3.8: Commit**

```bash
git add pdf_smasher/engine/_render_safety.py pdf_smasher/engine/rasterize.py pdf_smasher/engine/image_export.py tests/unit/engine/test_render_safety.py
git commit -m "feat(engine): share render-size cap between rasterize and image_export

Closes the bomb-check gap on the compress path: rasterize.py was
calling page.render with no pixel cap, while image_export.py had an
inline check. Both now route through check_render_size() in the new
_render_safety module."
```

---

## Task 4: Password threading through pikepdf open sites

**Scope decision (from design review):** there are two distinct routes that open user-supplied PDF bytes:

- **Compress route** (`compress()`): the per-page split opens the source via `pikepdf.open(...)` and writes each page as an unencrypted single-page slice. Downstream `rasterize_page` receives plaintext slices via `__init__.py:371` and `:552` — those callers pass `password=None`.
- **Image-export route** (`_run_image_export` → `iter_pages_as_images` → `_iter_pages_impl` → `_page_size_points` AND `rasterize_page`): operates directly on the user input bytes, never goes through the per-page split. Every layer in this chain must accept and forward `password`.

This is why `rasterize_page` itself needs a `password` kwarg (defaults to `None`): both routes call it. The compress callers pass `None` (slice is plaintext), the image-export caller forwards the user-supplied password. Same for `pdfium.PdfDocument`/`pypdfium2.PdfDocument` in `image_export.py`.

**Files:**
- Modify: `pdf_smasher/engine/triage.py:164` (signature) and line 171 (open call)
- Modify: `pdf_smasher/__init__.py:669` (public `triage` shim) and the per-page split via `pikepdf.open` near line 847
- Modify: `pdf_smasher/cli/main.py:498-501` (password-file read encoding)
- Create: `tests/unit/engine/test_password_plumbing.py`

- [ ] **Step 4.1: Write the failing test**

Make sure `tests/unit/engine/__init__.py` exists (Step 3.1 created it). Create `tests/unit/engine/test_password_plumbing.py`:

```python
"""Verify --password-file plumbs through to every pikepdf-open site."""

from __future__ import annotations

import io

import pikepdf
import pytest

from pdf_smasher import triage


def _make_encrypted_pdf(password: str, *, pages: int = 1) -> bytes:
    pdf = pikepdf.new()
    for _ in range(pages):
        pdf.add_blank_page(page_size=(612, 792))
    buf = io.BytesIO()
    pdf.save(buf, encryption=pikepdf.Encryption(owner=password, user=password, R=6))
    return buf.getvalue()


def test_triage_with_correct_password_succeeds() -> None:
    pdf_bytes = _make_encrypted_pdf("hunter2")
    report = triage(pdf_bytes, password="hunter2")
    assert report.classification != "require-password"
    assert report.is_encrypted is True


def test_triage_with_wrong_password_classifies_require_password() -> None:
    pdf_bytes = _make_encrypted_pdf("hunter2")
    report = triage(pdf_bytes, password="wrong")
    assert report.classification == "require-password"


def test_triage_with_no_password_classifies_require_password() -> None:
    # Regression coverage: existing behavior still works.
    pdf_bytes = _make_encrypted_pdf("hunter2")
    report = triage(pdf_bytes)
    assert report.classification == "require-password"


def test_triage_multipage_with_correct_password_returns_correct_page_count() -> None:
    pdf_bytes = _make_encrypted_pdf("hunter2", pages=5)
    report = triage(pdf_bytes, password="hunter2")
    assert report.classification != "require-password"
    assert report.pages == 5


def test_canonical_hash_with_correct_password_succeeds() -> None:
    """Forwarder: canonical_input_sha256 uses the password to open."""
    from pdf_smasher.engine.canonical import canonical_input_sha256

    pdf_bytes = _make_encrypted_pdf("hunter2")
    digest = canonical_input_sha256(pdf_bytes, password="hunter2")
    assert isinstance(digest, str) and len(digest) == 64


def test_image_export_with_correct_password_succeeds() -> None:
    """Image-export route: encrypted input + correct password → JPEG bytes out.

    Regression guard for the rasterize_page / _iter_pages_impl password
    threading. Without password threading on the image-export route,
    this fails inside _page_size_points or rasterize_page on the
    encrypted PDF.
    """
    from pdf_smasher.engine.image_export import iter_pages_as_images

    pdf_bytes = _make_encrypted_pdf("hunter2", pages=2)
    blobs = list(
        iter_pages_as_images(
            pdf_bytes,
            [0, 1],
            image_format="jpeg",
            dpi=72,
            password="hunter2",
        )
    )
    assert len(blobs) == 2
    # JPEG SOI marker
    assert all(b[:3] == b"\xff\xd8\xff" for b in blobs)


def test_compress_with_correct_password_succeeds() -> None:
    """End-to-end: encrypted input + correct password through compress()."""
    from pdf_smasher import compress
    from pdf_smasher.types import CompressOptions

    pdf_bytes = _make_encrypted_pdf("hunter2", pages=2)
    options = CompressOptions(password="hunter2", skip_verify=True)
    output, report = compress(pdf_bytes, options=options)
    # We don't assert ratio (compression on a blank-page fixture is
    # uninformative); we assert the compress pipeline COMPLETED rather
    # than refusing at the encrypted-input gate.
    assert isinstance(output, bytes)
    assert report.input_bytes == len(pdf_bytes)
```

The actual `iter_pages_as_images` signature (verified at `pdf_smasher/engine/image_export.py:58-73`) takes `page_indices: list[int]` as the 2nd positional argument and the rest as kw-only. The test above uses positional `[0, 1]` to match.

- [ ] **Step 4.2: Run, see it fail**

```bash
uv run pytest tests/unit/engine/test_password_plumbing.py -v
```

Expected:
- `test_triage_with_correct_password_succeeds` and `test_triage_with_wrong_password_classifies_require_password` and `test_triage_multipage_with_correct_password_returns_correct_page_count` FAIL with `TypeError: triage() got an unexpected keyword argument 'password'`.
- `test_triage_with_no_password_classifies_require_password` PASSES (pre-existing behavior).

- [ ] **Step 4.3: Add `password` kwarg to engine `triage()` and propagate `is_encrypted`**

In `pdf_smasher/engine/triage.py:164`, change the signature from:

```python
def triage(pdf_bytes: bytes) -> TriageReport:
```

to:

```python
def triage(pdf_bytes: bytes, *, password: str | None = None) -> TriageReport:
```

Inside the function, change line 171 from:

```python
pdf = pikepdf.open(io.BytesIO(pdf_bytes))
```

to:

```python
pdf = pikepdf.open(io.BytesIO(pdf_bytes), password=password or "")
```

**Important variable-reuse trap:** the existing local `is_encrypted` (line 169, init `False`) is fed into `_classify(is_encrypted=is_encrypted, ...)` at line 207-208. `_classify` returns `"require-password"` when `is_encrypted=True` (line 157). If we reuse the same local name to record "yes the input was encrypted but we got in", classification flips to `"require-password"` on the success path and breaks every test.

Use a SEPARATE local variable for the report-only field. As the first statement of the inner `try:` block (line 195 in the current file), immediately before `pages = len(pdf.pages)` (line 196), insert:

```python
report_is_encrypted = bool(pdf.is_encrypted)
```

Then change the `TriageReport(...)` construction at line 215 from:

```python
is_encrypted=is_encrypted,
```

to:

```python
is_encrypted=report_is_encrypted,
```

Leave the `_classify(is_encrypted=is_encrypted, ...)` call at line 207-208 UNCHANGED — `is_encrypted` stays `False` on the success path so `_classify` returns "proceed" (or whatever non-`require-password` classification is appropriate).

Net behavior:

| Input | Password supplied? | Correct? | `is_encrypted` |
|---|---|---|---|
| Plaintext PDF | n/a | n/a | `False` (pdfium reports `False`) |
| Encrypted PDF | No | n/a | `True` (PasswordError branch) |
| Encrypted PDF | Yes | No | `True` (PasswordError branch) |
| Encrypted PDF | Yes | Yes | **`True`** (new line — was `False` before) |

- [ ] **Step 4.4: Update every site that opens user-supplied PDF bytes**

The encrypted bytes flow through more than just `compress()`. Every site below reads user input directly (not a per-page plaintext slice). All must accept the password.

**(a) Public `triage` shim** — `pdf_smasher/__init__.py:669`:

```python
def triage(input_data: bytes, *, password: str | None = None) -> TriageReport:
    """Cheap structural scan. Never decodes image streams. See SPEC.md §4."""
    from pdf_smasher.engine.triage import triage as _triage

    return _triage(input_data, password=password)
```

**(b) `compress()` triage call** — `pdf_smasher/__init__.py:766` aliases `from pdf_smasher.engine.triage import triage as _triage`, and the call at line 771 reads `tri = _triage(input_data)`. Change to:

```python
tri = _triage(input_data, password=options.password)
```

(Use the `_triage` alias name verbatim — searching for `tri = triage(` would find no match because of the local rename.)

**(c) Per-page split via pikepdf.open** — `pdf_smasher/__init__.py:847`:

```python
with pikepdf.open(io.BytesIO(input_data), password=options.password or "") as _src_split:
```

**(d) Page-sizing pdfium open** — `pdf_smasher/__init__.py:834`:

```python
pdf_dims = pdfium.PdfDocument(input_data, password=options.password)
```

(`pypdfium2.PdfDocument` accepts `password=` directly, no normalization needed.)

**(e) Canonical hash open** — `pdf_smasher/engine/canonical.py:19`. Change the signature and inner open:

```python
def canonical_input_sha256(pdf_bytes: bytes, *, password: str | None = None) -> str:
    """Return the SHA-256 hex digest of a canonicalized form of ``pdf_bytes``."""
    with pikepdf.open(io.BytesIO(pdf_bytes), password=password or "") as pdf:
        ...
```

Then update its caller in `pdf_smasher/__init__.py` (search for `canonical_input_sha256(input_data)`) to forward `options.password`. (This caller currently soft-fails to `None` on `pikepdf.PdfError`; with the password threaded, encrypted-with-correct-password input will succeed.)

**(f) Image-export route — `_run_image_export`** — `pdf_smasher/cli/main.py:698-`. The function currently takes `(args, input_bytes, only_pages, image_format)` and does not look at the password. Inside the function body, near the top (before the `triage(input_bytes)` call at line 730), add:

```python
password = _read_password(args)
```

Pass it to BOTH downstream calls inside the function:

- The `triage(input_bytes)` call at line 730 → `tri = triage(input_bytes, password=password)`
- The `iter_pages_as_images(...)` call (around line 843) → add `password=password` to the kwargs

**(g) Image-export pdfium chain** — `pdf_smasher/engine/image_export.py` has a four-layer chain that all sees the user bytes. Thread the kwarg through every layer:

1. **`iter_pages_as_images(...)` line 58** — add `password: str | None = None` (kw-only) to the public signature; forward it to `_iter_pages_impl`.
2. **`_iter_pages_impl(...)` line 171** — add the same kwarg; forward to BOTH `_page_size_points` and `rasterize_page` (line 220).
3. **`_page_size_points(pdf_bytes, page_index)` line 155** — add `*, password: str | None = None`; pass to `pdfium.PdfDocument(pdf_bytes, password=password)` at line 159.
4. **`rasterize_page` is not in this file** — see (h) below.

**(h) Shared `rasterize_page` (used by BOTH routes)** — `pdf_smasher/engine/rasterize.py:15`. Change the signature from:

```python
def rasterize_page(pdf_bytes: bytes, *, page_index: int, dpi: int) -> Image.Image:
```

to:

```python
def rasterize_page(
    pdf_bytes: bytes,
    *,
    page_index: int,
    dpi: int,
    password: str | None = None,
) -> Image.Image:
```

Pass it through to `pdfium.PdfDocument(pdf_bytes, password=password)` at line 34.

Update the existing callers:

- `pdf_smasher/__init__.py:371` and `:552` (compress-route callers, which receive plaintext per-page slices) — pass `password=None` explicitly. Adding the explicit kwarg makes the plaintext-vs-encrypted invariant readable at the call site.
- `pdf_smasher/engine/image_export.py:220` — change to `rasterize_page(pdf_bytes, page_index=page_index, dpi=dpi, password=password)` so the image-export route forwards the user-supplied password.

- [ ] **Step 4.5: Fix CLI password-file read (Windows CRLF safe)**

In `pdf_smasher/cli/main.py`, find `_read_password` (around line 498). Change:

```python
return args.password_file.read_text().strip() or None
```

to:

```python
content = args.password_file.read_text(encoding="utf-8")
# Strip exactly one trailing newline (CR, LF, or CRLF). Don't use
# .strip() — that would also eat leading/trailing spaces inside the
# password itself.
if content.endswith("\r\n"):
    content = content[:-2]
elif content.endswith(("\n", "\r")):
    content = content[:-1]
return content or None
```

- [ ] **Step 4.5b: Verify every PDF-open site is accounted for**

```bash
grep -rn 'pikepdf\.open(\|pdfium\.PdfDocument(\|pypdfium2\.PdfDocument(' pdf_smasher/
```

Expect 9 matches (verified 2026-04-25). Each must fall into exactly one of these dispositions:

| File:line | Disposition |
|---|---|
| `pdf_smasher/engine/triage.py:171` | **EDIT** — Step 4.3 adds `password=password or ""` |
| `pdf_smasher/__init__.py:834` (`pdfium.PdfDocument(input_data)`) | **EDIT** — Step 4.4d adds `password=options.password` |
| `pdf_smasher/__init__.py:847` (per-page split `pikepdf.open(input_data)`) | **EDIT** — Step 4.4c adds `password=options.password or ""` |
| `pdf_smasher/__init__.py:1149` (post-compose `pikepdf.open(composed_bytes)`) | **SAFE** — already-composed plaintext output bytes |
| `pdf_smasher/engine/canonical.py:21` | **EDIT** — Step 4.4e adds `password=password or ""` |
| `pdf_smasher/engine/chunking.py:49` | **SAFE** — operates on composed output PDF bytes |
| `pdf_smasher/engine/text_layer.py:87` | **SAFE** — receives composed plaintext bytes |
| `pdf_smasher/engine/image_export.py:159` (`pdfium.PdfDocument(pdf_bytes)`) | **EDIT** — Step 4.4g adds `password=password` (image-export route receives encrypted user bytes here) |
| `pdf_smasher/engine/rasterize.py:34` (`pdfium.PdfDocument(pdf_bytes)`) | **EDIT** — Step 4.4h adds `password=password`; called by BOTH routes (compress callers pass `None`, image-export forwards the user password) |

If the grep returns a number other than 9, or any new site appears, stop and audit each one before continuing. Add a new row to the table and pick a disposition.

- [ ] **Step 4.6: Run new + baseline tests**

```bash
uv run pytest tests/unit -q
```

Expected: all green.

- [ ] **Step 4.7: Commit**

```bash
git add pdf_smasher/engine/triage.py pdf_smasher/engine/canonical.py \
        pdf_smasher/engine/image_export.py pdf_smasher/engine/rasterize.py \
        pdf_smasher/__init__.py pdf_smasher/cli/main.py \
        tests/unit/engine/test_password_plumbing.py
git commit -m "feat(security): plumb --password-file through every user-input PDF-open site

Every site that opens user-supplied PDF bytes — engine.triage,
__init__.compress (page-sizing pdfium open + per-page split),
engine.canonical (canonical_input_sha256), engine.image_export
(_run_image_export's pdfium open), the public triage() re-export,
and cli.main._run_image_export's triage call — now accepts a
password kwarg and forwards options.password / args password.
Per-page plaintext slices and composed-output opens are left alone.

triage() also propagates is_encrypted=True after a successful
password-decrypt (was False, masking real encryption status).

CLI password-file read switches from locale-decoded + .strip() to
utf-8-decoded + targeted CRLF/LF/CR strip so passwords with internal
whitespace are preserved and Windows-line-ending password files work."
```

---

## Task 5: Tighten input-size and page-count defaults; stat-before-read

**Files:**
- Modify: `pdf_smasher/types.py:69-70` (CompressOptions defaults)
- Modify: `pdf_smasher/cli/main.py:262-263` (argparse defaults) and around line 984 (stat-before-read)
- Create: `tests/unit/test_input_size_limits.py`

- [ ] **Step 5.1: Write the failing tests**

Create `tests/unit/test_input_size_limits.py`:

```python
"""Tighter defaults + stat-before-read on input cap."""

from __future__ import annotations

from pdf_smasher.types import CompressOptions


def test_default_max_input_mb_is_250() -> None:
    assert CompressOptions().max_input_mb == 250.0


def test_default_max_pages_is_10000() -> None:
    assert CompressOptions().max_pages == 10000


def test_cli_default_max_input_mb_is_250(capsys) -> None:
    from pdf_smasher.cli.main import _parser

    parser = _parser()
    ns = parser.parse_args(["dummy.pdf", "-o", "out.pdf"])
    assert ns.max_input_mb == 250.0


def test_cli_default_max_pages_is_10000(capsys) -> None:
    from pdf_smasher.cli.main import _parser

    parser = _parser()
    ns = parser.parse_args(["dummy.pdf", "-o", "out.pdf"])
    assert ns.max_pages == 10000
```

The factory `_parser()` is at `pdf_smasher/cli/main.py:201` and returns an `argparse.ArgumentParser`. The test imports it directly.

- [ ] **Step 5.2: Run, see it fail**

```bash
uv run pytest tests/unit/test_input_size_limits.py -v
```

Expected: FAIL on the new defaults.

- [ ] **Step 5.3: Update `CompressOptions` defaults (preserve `None` as escape hatch)**

In `pdf_smasher/types.py`, change:

```python
max_pages: int | None = None
max_input_mb: float = 2000.0
```

to:

```python
max_pages: int | None = 10000  # None disables the gate (programmatic-only escape hatch)
max_input_mb: float = 250.0
```

The type stays `int | None` so a programmatic caller doing `CompressOptions(max_pages=None)` can still opt into "unlimited". The CLI flag (Step 5.4) sets the default to `10000`, so flag users get the safer behavior; library users keep the existing escape hatch. Leave the `pdf_smasher/__init__.py:273` guard `if options.max_pages is not None and tri.pages > options.max_pages:` UNCHANGED — it still works correctly.

Update `tests/unit/test_types.py:24` in-place. The existing assertion lives inside the broader `test_compress_options_defaults` function (covers `engine`, `mode`, `target_bg_dpi`, etc.). Do NOT rename the function or extract it. Just edit line 24 from:

```python
    assert opts.max_input_mb == 2000.0
```

to:

```python
    assert opts.max_input_mb == 250.0
    assert opts.max_pages == 10000
```

(Adds one line after line 24; the broader test keeps its name and other assertions.)

- [ ] **Step 5.4: Update CLI argparse defaults**

In `pdf_smasher/cli/main.py`, change:

```python
p.add_argument("--max-pages", type=int)
p.add_argument("--max-input-mb", type=float, default=2000.0)
```

to:

```python
p.add_argument("--max-pages", type=int, default=10000)
p.add_argument("--max-input-mb", type=float, default=250.0)
```

- [ ] **Step 5.5: Stat-before-read (file-input branch only)**

In `pdf_smasher/cli/main.py:980-984`, the existing code is:

```python
if str(args.input) == "-":
    input_bytes = sys.stdin.buffer.read()
else:
    input_bytes = args.input.read_bytes()
```

Modify ONLY the `else:` branch to stat first. Do NOT add the stat call to the stdin branch (`Path("-").stat()` raises `FileNotFoundError`):

```python
if str(args.input) == "-":
    input_bytes = sys.stdin.buffer.read()
else:
    size_mb = args.input.stat().st_size / (1024 * 1024)
    if size_mb > args.max_input_mb:
        print(
            f"refused: input is {size_mb:.1f} MB, exceeds --max-input-mb={args.max_input_mb} "
            f"(default tightened from 2000.0; pass --max-input-mb 2000 to restore "
            f"the previous behavior)",
            file=sys.stderr,
        )
        return EXIT_OVERSIZE
    input_bytes = args.input.read_bytes()
```

`EXIT_OVERSIZE = 12` is defined at `pdf_smasher/cli/main.py:191`.

- [ ] **Step 5.5b: Enrich the in-engine refusal messages with override hint**

In `pdf_smasher/__init__.py`, find the two refusal messages introduced/touched here:

- Line ~274: `f"input has {tri.pages} pages; max_pages={options.max_pages}"` →
  `f"input has {tri.pages} pages; max_pages={options.max_pages} (default tightened from unlimited; pass --max-pages 100000 or set CompressOptions(max_pages=None) to relax)"`
- Line ~279: `f"input {input_mb:.1f} MB exceeds max_input_mb={options.max_input_mb}"` →
  `f"input {input_mb:.1f} MB exceeds max_input_mb={options.max_input_mb} (default tightened from 2000.0; pass --max-input-mb 2000 to relax)"`

Both messages survive into the `OversizeError` exception text and are user-facing.

- [ ] **Step 5.6: Run all tests**

```bash
uv run pytest tests/unit -q
```

Expected: all green.

- [ ] **Step 5.7: Commit**

```bash
git add pdf_smasher/types.py pdf_smasher/cli/main.py pdf_smasher/__init__.py \
        tests/unit/test_input_size_limits.py tests/unit/test_types.py
git commit -m "feat(security): tighten input-size defaults and stat-before-read

CompressOptions.max_input_mb 2000.0 → 250.0; CLI --max-pages default
None → 10000 (CompressOptions still accepts None as the unlimited
escape hatch). CLI stats the input file before slurping it into memory
and refuses with EXIT_OVERSIZE if it exceeds the cap. Refusal messages
include the override flag so users hitting the new cap know how to
restore the previous behavior."
```

---

## Task 6: Depth-cap fail-closed in triage

**Files:**
- Modify: `pdf_smasher/engine/triage.py` (`_walk_dict_for_names`)
- Create: `tests/unit/engine/test_triage_depth_cap.py`

- [ ] **Step 6.1: Write the failing test**

The actual signature is `_walk_dict_for_names(obj, target_names: frozenset[str], visited: set[int], depth=0, max_depth=12)` — `target_names` is positional, `visited` is REQUIRED, and keys are compared without the leading slash (`bare = str(key).lstrip("/")` at triage.py:53). The test must match.

Create `tests/unit/engine/test_triage_depth_cap.py`:

```python
"""Triage refuses to silently waive nested resource trees past the depth cap."""

from __future__ import annotations

import pytest

from pdf_smasher.exceptions import MaliciousPDFError
from pdf_smasher.engine.triage import _walk_dict_for_names


def _build_nested_dict(depth: int) -> dict[str, object]:
    """Build a chain dict 'depth' levels deep: {'nested': {'nested': {...}}}."""
    leaf: dict[str, object] = {}
    root = leaf
    for _ in range(depth):
        new_leaf: dict[str, object] = {}
        leaf["nested"] = new_leaf
        leaf = new_leaf
    return root


def test_walk_dict_at_cap_boundary_passes() -> None:
    # Calling with depth==max_depth must NOT raise (boundary is `>`, not `>=`).
    result = _walk_dict_for_names({}, frozenset({"JS"}), set(), depth=64, max_depth=64)
    assert result == set()


def test_walk_dict_one_past_cap_raises_malicious() -> None:
    # depth=65 with max_depth=64 must raise.
    with pytest.raises(MaliciousPDFError):
        _walk_dict_for_names({}, frozenset({"JS"}), set(), depth=65, max_depth=64)
```

Note: plain Python dicts aren't `pikepdf.Dictionary` instances, so the recursive descent at line 47 is skipped. The boundary tests above exercise the raise path directly by calling with explicit `depth=` and `max_depth=` values. The under-cap regression — that real `pikepdf.Dictionary` trees deeper than 12 but shallower than 64 don't get refused — is covered indirectly by the existing `tests/unit` baseline (245 tests across real and synthetic PDFs).

- [ ] **Step 6.2: Run, see it fail**

```bash
uv run pytest tests/unit/engine/test_triage_depth_cap.py -v
```

Expected:
- `test_walk_dict_at_cap_boundary_passes` PASSES (existing code already returns silently at exactly the cap).
- `test_walk_dict_one_past_cap_raises_malicious` FAILS — current `if depth > max_depth: return hits` early-returns silently instead of raising.

- [ ] **Step 6.3: Update `_walk_dict_for_names`**

In `pdf_smasher/engine/triage.py:36`, change the default `max_depth` from `12` to `64`. At line 45, replace `if depth > max_depth: return hits` with:

```python
if depth > max_depth:
    raise MaliciousPDFError(
        f"nested resource tree exceeds inspection depth ({max_depth}); refusing"
    )
```

Add `from pdf_smasher.exceptions import MaliciousPDFError` near the top of the file if not already imported.

The bump from 12 → 64 gives more headroom for legitimate deeply-nested PDFs (heavy form trees, tagged accessibility PDFs) while still bounding a recursion-bomb attempt.

- [ ] **Step 6.4: Run new + baseline**

```bash
uv run pytest tests/unit -q
```

Expected: all green.

- [ ] **Step 6.5: Commit**

```bash
git add pdf_smasher/engine/triage.py tests/unit/engine/test_triage_depth_cap.py
git commit -m "feat(security): depth-cap fail-closed in triage walker

_walk_dict_for_names previously returned silently past the depth cap,
which meant deeply nested JS / EmbeddedFiles entries skipped detection.
Now raises MaliciousPDFError. Cap raised from 12 to 64 to keep
legitimate heavily-nested PDFs (form trees, tagged accessibility)
unaffected."
```

---

## Task 7: O_NOFOLLOW on POSIX atomic write

**Files:**
- Modify: `pdf_smasher/utils/atomic.py`
- Create: `tests/unit/test_atomic_nofollow.py`

- [ ] **Step 7.1: Write the failing test (POSIX-only)**

Create `tests/unit/test_atomic_nofollow.py`:

```python
"""_atomic_write_bytes refuses to follow a pre-placed symlink."""

from __future__ import annotations

import os

import pytest

from pdf_smasher.utils.atomic import _atomic_write_bytes, PARTIAL_SUFFIX


@pytest.mark.skipif(os.name == "nt", reason="O_NOFOLLOW is POSIX-only")
def test_atomic_write_refuses_symlinked_partial_path(tmp_path) -> None:
    final = tmp_path / "out.pdf"
    partial = tmp_path / f"out.pdf{PARTIAL_SUFFIX}"
    bait = tmp_path / "bait.txt"
    bait.write_text("untouched")
    partial.symlink_to(bait)
    with pytest.raises(OSError):
        _atomic_write_bytes(final, b"hello")
    assert bait.read_text() == "untouched"


def test_atomic_write_happy_path_overwrites_pre_existing_partial(tmp_path) -> None:
    """Regression: a pre-existing NON-symlink partial gets overwritten cleanly."""
    final = tmp_path / "out.pdf"
    partial = tmp_path / f"out.pdf{PARTIAL_SUFFIX}"
    partial.write_bytes(b"stale")
    _atomic_write_bytes(final, b"fresh")
    assert final.read_bytes() == b"fresh"
    assert not partial.exists()
```

- [ ] **Step 7.2: Run, see it fail**

```bash
uv run pytest tests/unit/test_atomic_nofollow.py -v
```

Expected on macOS/Linux: FAIL — current implementation follows the symlink and overwrites `bait.txt`.

- [ ] **Step 7.3: Implement `O_NOFOLLOW`**

In `pdf_smasher/utils/atomic.py`, replace the body of `_atomic_write_bytes` with:

```python
import os
from pathlib import Path

PARTIAL_SUFFIX = ".partial"


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + PARTIAL_SUFFIX)
    if os.name != "nt":
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
        fd = os.open(str(tmp), flags, 0o644)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
    else:
        tmp.write_bytes(data)
    tmp.replace(path)
```

- [ ] **Step 7.4: Run new + baseline**

```bash
uv run pytest tests/unit -q
```

Expected: all green.

- [ ] **Step 7.5: Update `SECURITY.md`**

Add a short note (one or two lines) to `SECURITY.md` under a "Operational assumptions" or similar heading:

```
- The output directory passed via `-o`/`--output-dir` is assumed to be
  writable only by the running user. On POSIX, partial-write paths use
  `O_NOFOLLOW` so a pre-placed symlink at the partial path is refused.
```

- [ ] **Step 7.6: Commit**

```bash
git add pdf_smasher/utils/atomic.py tests/unit/test_atomic_nofollow.py SECURITY.md
git commit -m "feat(security): O_NOFOLLOW on POSIX atomic-write partial path

A pre-placed symlink at out.pdf.partial would previously redirect
the write. Now refuses with OSError. Windows path falls back to the
existing tmp.write_bytes (no NOFOLLOW equivalent without ctypes)."
```

---

## Task 8: Absolutize native-binary paths

**Files:**
- Modify: `pdf_smasher/engine/codecs/jbig2.py` (around line 56)
- Modify: `pdf_smasher/audit.py` (around line 37)

- [ ] **Step 8.1: Add a cached path-resolution helper that preserves the `None` fallback**

In `pdf_smasher/engine/codecs/jbig2.py`, replace the module-level `_JBIG2_BIN = "jbig2"` constant pattern with a cached resolver that returns `Optional[str]` so callers can keep the existing graceful fallback behavior (the engine warns and uses Flate instead of crashing when jbig2enc is absent — see `pdf_smasher/__init__.py:864` `jbig2enc-unavailable-using-flate-fallback`):

```python
import shutil
from functools import cache


@cache
def _resolve_jbig2_bin() -> str | None:
    """Resolve the jbig2 binary's absolute path once. None if not on PATH.

    Cached so subsequent subprocess calls skip the re-walk; cache is
    process-lifetime, which is fine because installing jbig2enc mid-
    process is not a supported scenario.
    """
    return shutil.which("jbig2")
```

Update every existing call site that uses the bare basename. For each `subprocess.run([_JBIG2_BIN, ...], ...)` (or equivalent), check the resolved path first:

```python
binary = _resolve_jbig2_bin()
if binary is None:
    raise FileNotFoundError("jbig2 not found on PATH; install jbig2enc")
subprocess.run([binary, ...], ...)
```

Existing callers that ALREADY check for absence (e.g., the warning-fallback path) should switch to `if _resolve_jbig2_bin() is None: ...` so the cached lookup is the single source of truth.

- [ ] **Step 8.2: Same pattern in `pdf_smasher/audit.py`**

In `pdf_smasher/audit.py`, the helper is `_probe_tool_version(binary)` (around line 26), not `_probe`. It already calls `shutil.which(binary)` to existence-check at line 34 and returns `"?"` if absent. Tighten line 38 from `subprocess.run([binary, "--version"], ...)` to:

```python
resolved = shutil.which(binary)
if resolved is None:
    return "?"
out = subprocess.run([resolved, "--version"], capture_output=True, check=False, ...)
```

(Move the existing `which` check into a single resolution step, then pass the resolved absolute path to `subprocess.run`. Net behavior is identical for the present-and-absent cases; the only change is that the running subprocess uses the absolute path.)

**Preserve `except subprocess.TimeoutExpired, OSError:` at audit.py:44 verbatim.** Python 3.14 (PEP 758) accepts unparenthesized `except` lists; the existing style in this file is intentional (see spec §1 context note). Do NOT "modernize" it to `except (subprocess.TimeoutExpired, OSError):`.

- [ ] **Step 8.3: Run baseline**

```bash
uv run pytest tests/unit -q
```

Expected: all green.

- [ ] **Step 8.4: Commit**

```bash
git add pdf_smasher/engine/codecs/jbig2.py pdf_smasher/audit.py
git commit -m "chore(security): resolve native binaries to absolute paths once

shutil.which() at first use; cached. Avoids re-walking PATH on every
subprocess call and makes the resolved path explicit in error messages."
```

---

## Task 9: Idempotent Pillow cap

**Files:**
- Modify: `pdf_smasher/_pillow_hardening.py`
- Modify: every `pdf_smasher/engine/*.py` that imports PIL — `background.py`, `compose.py`, `foreground.py`, `image_export.py`, `mask.py`, `ocr.py`, `rasterize.py`, `strategy.py`, `verifier.py` (call `ensure_capped()` at module top)
- Create: `tests/unit/test_pillow_cap_idempotent.py`

- [ ] **Step 9.1: Write the failing test**

Create `tests/unit/test_pillow_cap_idempotent.py`:

```python
"""Pillow cap installs whether or not pdf_smasher.__init__ ran."""

from __future__ import annotations


def test_ensure_capped_is_idempotent() -> None:
    import PIL.Image

    from pdf_smasher._pillow_hardening import MAX_IMAGE_PIXELS, ensure_capped

    ensure_capped()
    ensure_capped()
    assert PIL.Image.MAX_IMAGE_PIXELS == MAX_IMAGE_PIXELS
```

- [ ] **Step 9.2: Run, see it fail**

```bash
uv run pytest tests/unit/test_pillow_cap_idempotent.py -v
```

Expected: FAIL — `ImportError: cannot import name 'ensure_capped'`.

- [ ] **Step 9.3: Refactor `_pillow_hardening.py`**

Change the body to:

```python
from __future__ import annotations

import PIL.Image

from pdf_smasher._limits import MAX_BOMB_PIXELS

MAX_IMAGE_PIXELS: int = MAX_BOMB_PIXELS


def ensure_capped() -> None:
    """Idempotent installer of Pillow's decompression-bomb cap."""
    if PIL.Image.MAX_IMAGE_PIXELS != MAX_IMAGE_PIXELS:
        PIL.Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


# Side-effect call kept so `import pdf_smasher` still installs the cap;
# call sites that import only an engine submodule must call ensure_capped()
# themselves.
ensure_capped()
```

- [ ] **Step 9.4: Call `ensure_capped()` from every engine module that imports PIL**

Identify the modules first:

```bash
grep -l "^import PIL\|^from PIL" pdf_smasher/engine/*.py
```

Expected matches (all of these): `background.py`, `compose.py`, `foreground.py`, `image_export.py`, `mask.py`, `ocr.py`, `rasterize.py`, `strategy.py`, `verifier.py`.

At the top of each matched module, after the existing PIL import (the line may be `import PIL.Image`, `from PIL import Image`, `import PIL`, or any variant — match on whatever the file currently has), add:

```python
from pdf_smasher._pillow_hardening import ensure_capped

ensure_capped()
```

Place the import + call AFTER any PIL import so the cap is installed at module-load time on the same `PIL.Image` reference the module uses.

- [ ] **Step 9.5: Run all tests**

```bash
uv run pytest tests/unit -q
```

Expected: all green.

- [ ] **Step 9.6: Commit**

```bash
git add pdf_smasher/_pillow_hardening.py \
        pdf_smasher/engine/background.py \
        pdf_smasher/engine/compose.py \
        pdf_smasher/engine/foreground.py \
        pdf_smasher/engine/image_export.py \
        pdf_smasher/engine/mask.py \
        pdf_smasher/engine/ocr.py \
        pdf_smasher/engine/rasterize.py \
        pdf_smasher/engine/strategy.py \
        pdf_smasher/engine/verifier.py \
        tests/unit/test_pillow_cap_idempotent.py
git commit -m "chore(security): idempotent Pillow cap; engine modules self-install

Programmatic callers that import only an engine submodule (without
import pdf_smasher) now still get MAX_IMAGE_PIXELS set."
```

---

## Task 10: PyPI release workflow (dormant)

**Files:**
- Create: `.github/workflows/release.yml`
- Modify: `SECURITY.md` (point at the workflow)

- [ ] **Step 10.1: Write the workflow**

Create `.github/workflows/release.yml`:

```yaml
# Triggered only when a GitHub Release is published. Uses PyPI's OIDC
# Trusted Publisher (no long-lived API token). Configure the publisher
# entry at https://pypi.org/manage/account/publishing/ once before the
# first release.

name: release

on:
  release:
    types: [published]

permissions:
  contents: read

jobs:
  build-and-publish:
    name: Build sdist + wheel and publish to PyPI
    runs-on: ubuntu-latest
    permissions:
      id-token: write
      contents: read
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd  # v6
        with:
          persist-credentials: false
      - name: Install uv
        uses: astral-sh/setup-uv@37802adc94f370d6bfd71619e3f0bf239e1f3b78  # v7
        with:
          python-version: "3.14"
      - name: Build sdist + wheel
        run: uv build
      - name: Publish to PyPI via Trusted Publisher
        # TODO(human): verify this SHA matches the latest pypa/gh-action-pypi-publish
        # release before merging. `gh api repos/pypa/gh-action-pypi-publish/releases/latest --jq .tag_name`
        uses: pypa/gh-action-pypi-publish@76f52bc884231f62b9a034ebfe128415bbaabdfc  # v1.12.4
```

The `actions/checkout` and `astral-sh/setup-uv` SHAs are taken verbatim from `.github/workflows/ci.yml` so the cross-workflow pins stay aligned (the `versions-single-source` job in ci.yml only enforces the jbig2enc commit, but matching action pins by hand keeps the supply-chain story consistent). If the executing subagent has network access, `gh api repos/pypa/gh-action-pypi-publish/releases/latest --jq .tag_name` confirms the latest release tag — bump the pin if the comment date drifts. If offline, leave the pin and the `TODO(human)` marker in place; Jack will verify pre-merge.

- [ ] **Step 10.2: Reference from SECURITY.md**

In `SECURITY.md`, find the section that mentions "PyPI" and OIDC trusted publishing. Point it at `.github/workflows/release.yml`. One-line change.

- [ ] **Step 10.3: Lint the workflow**

```bash
pipx run zizmor --min-severity=medium .github/workflows/release.yml
```

Expected: no medium-or-higher findings.

```bash
pipx run actionlint .github/workflows/release.yml
```

Expected: no errors.

- [ ] **Step 10.4: Run all tests**

```bash
uv run pytest tests/unit -q
```

Expected: all green.

- [ ] **Step 10.5: Commit**

```bash
git add .github/workflows/release.yml SECURITY.md
git commit -m "feat(ci): add dormant PyPI release workflow with OIDC trusted publishing

Triggered only by published GitHub Releases. No PYPI_API_TOKEN secret
introduced. Publisher entry must be configured on pypi.org before the
first release tag is cut."
```

---

## Task 11: Dependabot pre-commit ecosystem

**Files:**
- Modify: `.github/dependabot.yml`

- [ ] **Step 11.1: Add a fourth ecosystem block**

At the bottom of `.github/dependabot.yml`, add:

```yaml
  - package-ecosystem: "pre-commit"
    directory: "/"
    schedule:
      interval: "weekly"
    open-pull-requests-limit: 5
    labels:
      - "dependencies"
      - "pre-commit"
```

- [ ] **Step 11.2: Verify YAML is valid**

```bash
pipx run yamllint .github/dependabot.yml
```

Expected: no errors. (If yamllint is fussy about line length, that's fine; only structural errors block.)

- [ ] **Step 11.3: Commit**

```bash
git add .github/dependabot.yml
git commit -m "chore(ci): add pre-commit ecosystem to dependabot

Keeps .pre-commit-config.yaml rev: pins fresh in line with the rest
of the supply-chain hardening."
```

---

## Task 12: CODE_OF_CONDUCT.md

**Files:**
- Create: `CODE_OF_CONDUCT.md`

- [ ] **Step 12.1: Fetch the canonical text and customize**

```bash
curl -fsSL https://www.contributor-covenant.org/version/2/1/code_of_conduct/code_of_conduct.md -o CODE_OF_CONDUCT.md
```

After fetch, open `CODE_OF_CONDUCT.md` and find the line in the **Enforcement** section that reads (verbatim): `Instances of abusive, harassing, or otherwise unacceptable behavior may be reported to the community leaders responsible for enforcement at [INSERT CONTACT METHOD].`

Replace `[INSERT CONTACT METHOD]` with: `https://github.com/hank-ai/hankpdf/security/advisories/new`.

If `curl` is unavailable, copy the canonical text manually from the URL above. The file's exact content is permissive (CC BY 4.0).

- [ ] **Step 12.2: Verify**

```bash
test -f CODE_OF_CONDUCT.md && echo "ok"
```

Expected: `ok`.

- [ ] **Step 12.3: Commit**

```bash
git add CODE_OF_CONDUCT.md
git commit -m "docs: add Contributor Covenant 2.1"
```

---

## Task 13: Issue and PR templates

**Files:**
- Create: `.github/ISSUE_TEMPLATE/bug_report.yml`
- Create: `.github/ISSUE_TEMPLATE/feature_request.yml`
- Create: `.github/ISSUE_TEMPLATE/config.yml`
- Create: `.github/PULL_REQUEST_TEMPLATE.md`

- [ ] **Step 13.1: Bug report template**

Create `.github/ISSUE_TEMPLATE/bug_report.yml`:

```yaml
name: Bug report
description: Something isn't working
labels: [bug]
body:
  - type: textarea
    id: what-happened
    attributes:
      label: What happened?
      description: A clear and concise description of the bug.
    validations:
      required: true
  - type: textarea
    id: hankpdf-version
    attributes:
      label: hankpdf --version + hankpdf --doctor output
      description: Paste both. --doctor includes native dep versions (tesseract, jbig2enc, qpdf) which are critical for diagnosis.
      render: shell
    validations:
      required: true
  - type: input
    id: os
    attributes:
      label: OS / platform
      description: e.g. macOS 14.5 (arm64), Ubuntu 24.04, Windows 11
    validations:
      required: true
  - type: input
    id: correlation-id
    attributes:
      label: Correlation ID
      description: From the run's stderr (e.g. cid=abc123). Optional but very helpful.
  - type: textarea
    id: input-shape
    attributes:
      label: Input PDF characteristics
      description: Page count, scan vs digital-native, encrypted, signed, approximate input MB. Do not attach PDFs containing private data.
```

- [ ] **Step 13.2: Feature request template**

Create `.github/ISSUE_TEMPLATE/feature_request.yml`:

```yaml
name: Feature request
description: Suggest an enhancement
labels: [enhancement]
body:
  - type: textarea
    id: problem
    attributes:
      label: What problem are you trying to solve?
    validations:
      required: true
  - type: textarea
    id: proposal
    attributes:
      label: Proposed solution
  - type: textarea
    id: alternatives
    attributes:
      label: Alternatives considered
```

- [ ] **Step 13.3: Issue template config**

Create `.github/ISSUE_TEMPLATE/config.yml`:

```yaml
blank_issues_enabled: false
contact_links:
  - name: Security report
    url: https://github.com/hank-ai/hankpdf/security/advisories/new
    about: Please report security issues privately via GitHub Security Advisories.
```

- [ ] **Step 13.4: PR template**

Create `.github/PULL_REQUEST_TEMPLATE.md`:

```markdown
## Summary

<!-- 1-3 sentences. What changed and why. -->

## Test plan

- [ ] `uv run pytest tests/unit -q` (all green)
- [ ] `uv run ruff check pdf_smasher tests`
- [ ] `uv run ruff format --check pdf_smasher tests`
- [ ] `uv run mypy pdf_smasher`
- [ ] Conventional Commits prefix on every commit (feat / fix / chore / docs / refactor / test / perf / security / observability / diag — see `CONTRIBUTING.md`)

## Notes for reviewers

<!-- Anything subtle, breaking, or worth a second look. -->
```

- [ ] **Step 13.5: Verify**

```bash
ls .github/ISSUE_TEMPLATE/
test -f .github/PULL_REQUEST_TEMPLATE.md && echo "ok"
```

Expected: 3 files in `ISSUE_TEMPLATE/`, `ok` for PR template.

- [ ] **Step 13.6: Commit**

```bash
git add .github/ISSUE_TEMPLATE/ .github/PULL_REQUEST_TEMPLATE.md
git commit -m "docs(github): add issue templates and PR template"
```

---

## Task 14: README accuracy

**Files:**
- Modify: `README.md`

- [ ] **Step 14.1: Update test count**

Find the status line near the top of `README.md` (line ~7) that says "327 tests passing on Linux / macOS / Windows CI". Change to "340 tests passing on Linux / macOS / Windows CI".

Also update the matching `pytest -q` example comment (around line 248) if it includes a count.

- [ ] **Step 14.2: Reconcile GHCR claim**

Run:

```bash
gh api /orgs/hank-ai/packages/container/hankpdf/versions --jq 'length' 2>&1 || echo "no GHCR package yet"
```

If the package exists with versions: in the status line, change "Not yet published to PyPI or GHCR" to "Not yet published to PyPI; GHCR images are built from `main`."

If the package does not exist yet: keep "Not yet published to PyPI or GHCR" but add an inline parenthetical "(GHCR pushes are wired up in `docker.yml` and run on the next merge to `main`)".

- [ ] **Step 14.3: Fix Docker image tag examples (both occurrences)**

Find both occurrences of `ghcr.io/hank-ai/hankpdf:v0.0.1` in `README.md`:

```bash
grep -n "v0\.0\.1" README.md
```

Two occurrences (around lines 109 and 124). The surrounding copy at line 105 says **"For production use, pin to an immutable tag or digest"** — so changing line 109 to `:latest` would directly contradict the paragraph. Use a placeholder instead so the example still says "pin to a real version" without falsely claiming `v0.0.1` exists:

- **Line 109** (`docker pull` example): change `ghcr.io/hank-ai/hankpdf:v0.0.1` → `ghcr.io/hank-ai/hankpdf:<version-tag>` and add an inline comment in the surrounding text: `# Replace <version-tag> with a published tag, e.g. v0.1.0; see GitHub Releases.`
- **Line 124** (`cosign verify` example): same — change `:v0.0.1` → `:<version-tag>`. The cosign command keeps its meaning (verify whatever tag you choose).

If line 105's "pin to immutable tag or digest" copy already shows the SHA-digest example below it (verified at line 112: `docker pull ghcr.io/hank-ai/hankpdf@sha256:<digest>`), the placeholder approach reads naturally next to it.

- [ ] **Step 14.4: Verify**

```bash
grep -n "327 tests\|v0\.0\.1" README.md
```

Expected: no matches.

- [ ] **Step 14.5: Commit**

```bash
git add README.md
git commit -m "docs(readme): update test count, reconcile GHCR claim, fix docker tag examples"
```

---

## Task 15: CHANGELOG + ARCHITECTURE render-safety section

**Files:**
- Create: `CHANGELOG.md`
- Modify: `docs/ARCHITECTURE.md`

- [ ] **Step 15.1: Create `CHANGELOG.md`**

Create `CHANGELOG.md` at repo root (Keep a Changelog format):

```markdown
# Changelog

All notable changes to `pdf-smasher` (HankPDF) are documented here. The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows pre-1.0 SemVer (anything may break between minor versions until 1.0).

## [Unreleased]

### Added
- Shared render-size cap helper (`pdf_smasher.engine._render_safety.check_render_size`) used by both the compress (`rasterize.py`) and image-export (`image_export.py`) paths. Closes a decompression-bomb gap on the compress path.
- `--password-file` now plumbs the password through to every PDF-open site that touches user-supplied encrypted bytes (`engine.triage.triage`, public `pdf_smasher.triage`, and the per-page split in `compress`).
- `_walk_dict_for_names` in triage now fails closed past its depth cap (raises `MaliciousPDFError` instead of silently early-returning); cap raised from 12 to 64 for legitimate-PDF headroom.
- POSIX `O_NOFOLLOW` on the partial-write path in `pdf_smasher.utils.atomic._atomic_write_bytes`. A pre-placed symlink at the partial path is now refused. Windows path is unchanged (no `O_NOFOLLOW` equivalent without ctypes).
- Idempotent `pdf_smasher._pillow_hardening.ensure_capped()`. Engine modules that import PIL now self-install the cap so programmatic callers using only an engine submodule still get the protection.
- Native binary paths (`jbig2`, `tesseract`, `qpdf`) resolved to absolute paths once via cached `shutil.which`.
- `.github/workflows/release.yml` — dormant PyPI release workflow with OIDC trusted publishing. Triggered only by published GitHub Releases. No `PYPI_API_TOKEN` secret is introduced. Configure the publisher entry on pypi.org once before cutting the first release.
- `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1).
- `.github/ISSUE_TEMPLATE/{bug_report,feature_request,config}.yml` and `.github/PULL_REQUEST_TEMPLATE.md`.
- `pre-commit` ecosystem in `.github/dependabot.yml`.
- `docs/ARCHITECTURE.md` — new "Render-size protection" section documenting the two-tier cap (`_render_safety.check_render_size` pre-allocation + Pillow `MAX_IMAGE_PIXELS` post-decode).

### Changed
- **BREAKING (CLI):** `--max-input-mb` default lowered from `2000.0` to `250.0`. To restore previous behavior: `--max-input-mb 2000`.
- **BREAKING (CLI):** `--max-pages` default lowered from "unlimited" to `10000`. To restore previous behavior: `--max-pages 100000` (or higher).
- **Library API note:** `CompressOptions.max_input_mb` default also tightened to `250.0`. `CompressOptions.max_pages` default tightened from `None` to `10000`; the type stays `int | None`, so programmatic callers can still pass `max_pages=None` to opt into the previous unlimited behavior.
- CLI `--password-file` read switched from locale-default decoding + `.strip()` to UTF-8 decoding with targeted CR/LF/CRLF stripping. Passwords with internal whitespace are now preserved; Windows-line-ending password files now work.
- Refusal messages for both `max_input_mb` and `max_pages` now include the override flag so users hitting the new caps know how to relax them.

### Security
- New POSIX `O_NOFOLLOW` defense (see Added).
- Triage depth-cap walker now fails closed (see Added).
- Decompression-bomb pre-allocation cap now applied on the compress path (previously only the image-export path).

### Repository
- Replaced placeholder `ourorg/pdf-smasher` URLs with the real `hank-ai/hankpdf` URLs across `pyproject.toml`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`.
- Removed placeholder `security@TBD.example` from `SECURITY.md`. GitHub Security Advisories is now the sole reporting channel.
- Storage-agnostic corpus mirror story (`s3_mirror` field renamed to `mirror_url`; docs no longer assume S3).
- README test count and Docker-image tag examples updated to reflect reality.
```

- [ ] **Step 15.2: Add a "Render-size protection" section to `docs/ARCHITECTURE.md`**

Append (or splice into the appropriate Engine / Safety subsection of) `docs/ARCHITECTURE.md`:

```markdown
### Render-size protection (two-tier)

Two independent caps protect against decompression-bomb PDFs that would
allocate billions of pixels:

1. **Pre-allocation pixel-count guard.** `pdf_smasher.engine._render_safety.check_render_size(width_pt, height_pt, dpi)` is called BEFORE pdfium allocates the bitmap. It computes the target pixel dimensions from the page geometry and refuses with `pdf_smasher.exceptions.DecompressionBombError` if the product would exceed `pdf_smasher._limits.MAX_BOMB_PIXELS` (~715 Mpx, sized so an RGB raster fits in 2 GiB). Both the compress path (`rasterize.rasterize_page`) and the image-export path (`image_export._iter_pages_impl`) call this helper. Tests in `tests/unit/engine/test_render_safety.py`.
2. **Post-decode Pillow guard.** `PIL.Image.MAX_IMAGE_PIXELS` is set to the SAME value by `pdf_smasher._pillow_hardening.ensure_capped()` so any image opened through Pillow (e.g., a per-page raster being re-encoded) hits the same ceiling. Pillow raises `PIL.Image.DecompressionBombError`, which our engine modules re-raise as our typed `DecompressionBombError` for consistent CLI exit-code mapping (`EXIT_DECOMPRESSION_BOMB=16`).

Both caps share `pdf_smasher._limits.MAX_BOMB_PIXELS` as the canonical numeric value — the `tests/unit/test_pillow_hardening.py` suite asserts they don't drift apart.

Callers that knowingly need a higher ceiling (e.g., a future per-page render CLI for engineering drawings) can pass `max_pixels=N` to `check_render_size` for a per-call override; there is intentionally no override for the Pillow cap (it's a global SECURITY boundary, not a tuning knob).
```

- [ ] **Step 15.3: Verify and commit**

```bash
test -f CHANGELOG.md && grep -q "Render-size protection" docs/ARCHITECTURE.md && echo "ok"
```

Expected: `ok`.

```bash
git add CHANGELOG.md docs/ARCHITECTURE.md
git commit -m "docs: changelog + ARCHITECTURE render-safety section

Captures the user-facing breaking changes (--max-input-mb,
--max-pages defaults) with override hints, and documents the
two-tier render-size protection (pre-allocation pixel guard +
Pillow post-decode cap) so future contributors don't get
confused by overlapping DecompressionBombError types."
```

---

## Final verification

- [ ] **Run all tests**

```bash
uv run pytest tests/unit -q
```

Expected: green.

- [ ] **Run lint**

```bash
uv run ruff check pdf_smasher tests
uv run ruff format --check pdf_smasher tests
uv run mypy pdf_smasher
```

Expected: green.

- [ ] **Acceptance grep checks**

```bash
grep -rn "ourorg\|TBD.example\|shartzog\|s3_mirror\|our-corpus-bucket" \
  --include="*.md" --include="*.toml" --include="*.py" --include="*.yml" --include="*.json" \
  --exclude-dir=.git --exclude-dir=.claude --exclude-dir=docs/superpowers/specs .
```

Expected: no matches. (The spec under `docs/superpowers/specs/` and any local-only `.claude/` checkpoints are excluded; both legitimately reference these strings.)

```bash
git log --all -p | grep -E "shartzog|TBD\.example" | head -5 || true
```

Note: the `0a966fe` commit body contains the `shartzog` reference. Per spec §6, we accept this — rewriting commit-message bodies via force-push is louder than the leak. This is informational only; do not block on it.

- [ ] **Confirm new files exist**

```bash
ls CHANGELOG.md \
   CODE_OF_CONDUCT.md \
   .github/workflows/release.yml \
   .github/PULL_REQUEST_TEMPLATE.md \
   .github/ISSUE_TEMPLATE/bug_report.yml \
   .github/ISSUE_TEMPLATE/feature_request.yml \
   .github/ISSUE_TEMPLATE/config.yml \
   pdf_smasher/engine/_render_safety.py \
   tests/unit/engine/test_render_safety.py \
   tests/unit/engine/test_password_plumbing.py \
   tests/unit/engine/test_triage_depth_cap.py \
   tests/unit/test_atomic_nofollow.py \
   tests/unit/test_input_size_limits.py \
   tests/unit/test_pillow_cap_idempotent.py
```

Expected: all listed.

- [ ] **Done**

Hand back to /jack-it-up Phase 5 (`/dc` review of the implementation).
