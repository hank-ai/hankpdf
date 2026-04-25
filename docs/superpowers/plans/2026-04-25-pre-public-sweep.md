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

**Modify:**
- `pyproject.toml`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `docs/TROUBLESHOOTING.md`, `SECURITY.md`, `CONTRIBUTING.md`, `README.md`, `.gitignore`, `.github/dependabot.yml`
- `tests/STRATEGY.md`, `scripts/fetch_corpus.py`
- `pdf_smasher/types.py` (defaults)
- `pdf_smasher/cli/main.py` (defaults, password file read, stat-before-read)
- `pdf_smasher/__init__.py` (password threading)
- `pdf_smasher/engine/triage.py` (password kwarg, depth-cap fail-closed)
- `pdf_smasher/engine/rasterize.py` (use `_render_safety`, accept password)
- `pdf_smasher/engine/image_export.py` (use `_render_safety`, accept password)
- `pdf_smasher/utils/atomic.py` (`O_NOFOLLOW` on POSIX)
- `pdf_smasher/_pillow_hardening.py` (idempotent `ensure_capped()`)
- `pdf_smasher/engine/codecs/jbig2.py`, `pdf_smasher/audit.py` (absolute binary paths)

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
grep -rn "ourorg\|TBD.example\|shartzog" --include="*.md" --include="*.toml" --include="*.py" --include="*.yml" .
```

Expected output: no matches outside `docs/superpowers/specs/` and `.git/`. (The spec file references these strings explicitly.)

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

- [ ] **Step 3.1: Write the failing test**

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


def test_zero_dpi_does_not_underflow() -> None:
    # Zero DPI -> zero pixels; not useful but must not raise.
    check_render_size(width_pt=612.0, height_pt=792.0, dpi=0.0)
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
"""

from __future__ import annotations

from pdf_smasher._limits import MAX_BOMB_PIXELS
from pdf_smasher.exceptions import DecompressionBombError

_POINTS_PER_INCH: float = 72.0


def check_render_size(width_pt: float, height_pt: float, dpi: float) -> None:
    """Refuse if rasterizing the page at ``dpi`` would exceed the pixel cap.

    ``DecompressionBombError`` is raised before any allocation happens.
    The CLI maps the exception to ``EXIT_DECOMPRESSION_BOMB=16``.
    """
    target_w = round(width_pt * dpi / _POINTS_PER_INCH)
    target_h = round(height_pt * dpi / _POINTS_PER_INCH)
    if target_w * target_h > MAX_BOMB_PIXELS:
        raise DecompressionBombError(
            f"page would render to {target_w}x{target_h} pixels "
            f"({target_w * target_h:,} px), exceeding cap of {MAX_BOMB_PIXELS:,}"
        )
```

- [ ] **Step 3.4: Run test, see it pass**

```bash
uv run pytest tests/unit/engine/test_render_safety.py -v
```

Expected: 4 passed.

- [ ] **Step 3.5: Wire helper into `rasterize.py`**

In `pdf_smasher/engine/rasterize.py`, after the `page = pdf[page_index]` line and before `bitmap = page.render(...)`, add:

```python
from pdf_smasher.engine._render_safety import check_render_size  # at top of file

# inside the function, before page.render:
check_render_size(width_pt=width_pt, height_pt=height_pt, dpi=dpi)
```

Use the existing `width_pt, height_pt = page.get_size()` and `dpi` parameter already in scope.

- [ ] **Step 3.6: Wire helper into `image_export.py` and remove the inline check**

In `pdf_smasher/engine/image_export.py`, find the existing inline check (around line 208) that compares `target_w * target_h > _MAX_BOMB_PIXELS`. Replace it with a call to `check_render_size(width_pt=..., height_pt=..., dpi=...)` using the same inputs that produced `target_w` and `target_h`. Drop the now-unused `_MAX_BOMB_PIXELS` local if it's no longer referenced (the canonical home is `pdf_smasher._limits.MAX_BOMB_PIXELS`).

Add `from pdf_smasher.engine._render_safety import check_render_size` at the top.

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

## Task 4: Password threading through PDF-open sites

**Files:**
- Modify: `pdf_smasher/engine/triage.py:164` (signature) and line 171 (open call)
- Modify: `pdf_smasher/__init__.py` (call site to triage; per-page split via pikepdf.open near line 847)
- Modify: `pdf_smasher/engine/rasterize.py` (accept password kwarg, pass to `pdfium.PdfDocument`)
- Modify: `pdf_smasher/engine/image_export.py` (accept password kwarg, pass to `pypdfium2.PdfDocument`)
- Modify: `pdf_smasher/cli/main.py:498-501` (password-file read encoding)
- Create: `tests/unit/engine/test_password_plumbing.py`

- [ ] **Step 4.1: Write the failing test**

Create `tests/unit/engine/test_password_plumbing.py`:

```python
"""Verify --password-file plumbs through to every PDF-open site."""

from __future__ import annotations

import io

import pikepdf
import pytest

import pdf_smasher
from pdf_smasher import compress, triage
from pdf_smasher.exceptions import EncryptedPDFError
from pdf_smasher.types import CompressOptions


def _make_encrypted_pdf(password: str) -> bytes:
    """Build a 1-page encrypted PDF in memory."""
    pdf = pikepdf.new()
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
    pdf_bytes = _make_encrypted_pdf("hunter2")
    report = triage(pdf_bytes)
    assert report.classification == "require-password"


def test_compress_passes_password_through_to_engine() -> None:
    pdf_bytes = _make_encrypted_pdf("hunter2")
    options = CompressOptions(password="hunter2", skip_verify=True)
    # This should NOT raise EncryptedPDFError; it should proceed past triage.
    # We don't care about the final ratio for this test — only that the
    # password reached the engine.
    try:
        compress(pdf_bytes, options=options)
    except pdf_smasher.CompressError as exc:
        # Acceptable: a tiny blank-page PDF may fail downstream gates.
        # The point is it didn't fail at the password gate.
        assert not isinstance(exc, EncryptedPDFError), (
            f"password should have unlocked the PDF: {exc}"
        )
```

- [ ] **Step 4.2: Run, see it fail**

```bash
uv run pytest tests/unit/engine/test_password_plumbing.py -v
```

Expected: FAIL with `TypeError: triage() got an unexpected keyword argument 'password'`.

- [ ] **Step 4.3: Add `password` kwarg to `triage()`**

In `pdf_smasher/engine/triage.py`, change the signature from:

```python
def triage(pdf_bytes: bytes) -> TriageReport:
```

to:

```python
def triage(pdf_bytes: bytes, *, password: str | None = None) -> TriageReport:
```

Inside the function, change the line that opens the PDF (currently `pdf = pikepdf.open(io.BytesIO(pdf_bytes))` around line 171) to:

```python
pdf = pikepdf.open(io.BytesIO(pdf_bytes), password=password or "")
```

- [ ] **Step 4.4: Pass password from `compress()` to `triage()`**

In `pdf_smasher/__init__.py`, find the `triage(input_data)` call inside `compress()`. Change it to `triage(input_data, password=options.password)`.

In the same file, find the per-page split site that calls `pikepdf.open(io.BytesIO(input_data))` (around line 847). Change it to `pikepdf.open(io.BytesIO(input_data), password=options.password or "")`.

- [ ] **Step 4.5: Pass password to pdfium-based render call sites**

In `pdf_smasher/engine/rasterize.py`, change `rasterize_page` signature to accept `*, password: str | None = None` and pass it as `pdfium.PdfDocument(pdf_bytes, password=password)`.

In `pdf_smasher/engine/image_export.py`, find the `pypdfium2.PdfDocument(...)` call (around line 159) and pass `password=options.password` into it (the `options` parameter is already in scope).

Update the call site in `pdf_smasher/__init__.py` that calls `rasterize_page` to forward `options.password`.

- [ ] **Step 4.6: Fix CLI password-file read**

In `pdf_smasher/cli/main.py`, find `_read_password` (around line 498). Change:

```python
return args.password_file.read_text().strip() or None
```

to:

```python
content = args.password_file.read_text(encoding="utf-8")
return content.removesuffix("\n") or None
```

- [ ] **Step 4.7: Run new + baseline tests**

```bash
uv run pytest tests/unit -q
```

Expected: all green.

- [ ] **Step 4.8: Commit**

```bash
git add pdf_smasher/engine/triage.py pdf_smasher/engine/rasterize.py pdf_smasher/engine/image_export.py pdf_smasher/__init__.py pdf_smasher/cli/main.py tests/unit/engine/test_password_plumbing.py
git commit -m "feat(security): plumb --password-file through every PDF-open site

triage() now accepts password=. compress() forwards options.password
to triage() and to per-page pikepdf.open + pdfium.PdfDocument calls.
CLI password-file read uses utf-8 + removesuffix instead of locale +
strip."
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

- [ ] **Step 5.3: Update `CompressOptions` defaults**

In `pdf_smasher/types.py`, change:

```python
max_pages: int | None = None
max_input_mb: float = 2000.0
```

to:

```python
max_pages: int = 10000
max_input_mb: float = 250.0
```

In `pdf_smasher/__init__.py:273`, simplify the guard from:

```python
if options.max_pages is not None and tri.pages > options.max_pages:
```

to:

```python
if tri.pages > options.max_pages:
```

(The `is not None` check was the only consumer of the Optional type. After this change, mypy is happy and the guard fires whenever the flag value — default 10000 — is exceeded.)

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

- [ ] **Step 5.5: Stat-before-read**

In `pdf_smasher/cli/main.py`, find the line `input_bytes = args.input.read_bytes()` (around line 984). Insert a stat-check before it:

```python
size_mb = args.input.stat().st_size / (1024 * 1024)
if size_mb > args.max_input_mb:
    print(
        f"refused: input is {size_mb:.1f} MB, exceeds --max-input-mb={args.max_input_mb}",
        file=sys.stderr,
    )
    return EXIT_OVERSIZE  # use whatever the existing oversize exit code is named
input_bytes = args.input.read_bytes()
```

The oversize exit code is `EXIT_OVERSIZE = 12` (defined at `pdf_smasher/cli/main.py:191`).

- [ ] **Step 5.6: Run all tests**

```bash
uv run pytest tests/unit -q
```

Expected: all green.

- [ ] **Step 5.7: Commit**

```bash
git add pdf_smasher/types.py pdf_smasher/cli/main.py tests/unit/test_input_size_limits.py
git commit -m "feat(security): tighten input-size defaults and stat-before-read

max_input_mb default 2000.0 → 250.0; max_pages default None → 10000.
CLI now stats the input file before slurping it into memory and
refuses with EXIT_OVERSIZE if it exceeds the cap."
```

---

## Task 6: Depth-cap fail-closed in triage

**Files:**
- Modify: `pdf_smasher/engine/triage.py` (`_walk_dict_for_names`)
- Create: `tests/unit/engine/test_triage_depth_cap.py`

- [ ] **Step 6.1: Write the failing test**

Create `tests/unit/engine/test_triage_depth_cap.py`:

```python
"""Triage refuses to silently waive nested resource trees past the depth cap."""

from __future__ import annotations

import io

import pikepdf
import pytest

from pdf_smasher.exceptions import MaliciousPDFError
from pdf_smasher.engine.triage import _walk_dict_for_names


def _build_nested_dict(depth: int) -> dict:
    inner: dict = {}
    cur = inner
    for _ in range(depth):
        nxt: dict = {}
        cur["nested"] = nxt
        cur = nxt
    return inner


def test_walk_dict_under_cap_succeeds() -> None:
    d = _build_nested_dict(20)
    list(_walk_dict_for_names(d, target_names={"/JS"}))


def test_walk_dict_over_cap_raises_malicious() -> None:
    d = _build_nested_dict(64)
    with pytest.raises(MaliciousPDFError):
        list(_walk_dict_for_names(d, target_names={"/JS"}))
```

- [ ] **Step 6.2: Run, see it fail**

```bash
uv run pytest tests/unit/engine/test_triage_depth_cap.py -v
```

Expected: the under-cap test passes (current `max_depth=12` is too low — adjust if it fails). The over-cap test fails because the function returns silently instead of raising.

- [ ] **Step 6.3: Update `_walk_dict_for_names`**

In `pdf_smasher/engine/triage.py`, find `_walk_dict_for_names`. Change the default `max_depth` from `12` to `32`. At the depth-cap branch (where the function currently early-returns past `max_depth`), raise instead:

```python
if depth > max_depth:
    raise MaliciousPDFError(
        f"nested resource tree exceeds inspection depth ({max_depth}); refusing"
    )
```

Add `from pdf_smasher.exceptions import MaliciousPDFError` if not already imported.

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
Now raises MaliciousPDFError. Cap raised from 12 to 32 to keep
legitimate PDFs unaffected."
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

- [ ] **Step 8.1: Add a path-resolution helper**

In `pdf_smasher/engine/codecs/jbig2.py`, replace the module-level `_JBIG2_BIN = "jbig2"` constant pattern with a cached resolver:

```python
import shutil
from functools import cache


@cache
def _resolve_jbig2_bin() -> str:
    found = shutil.which("jbig2")
    if not found:
        raise FileNotFoundError("jbig2 not found on PATH; install jbig2enc")
    return found
```

Replace every `subprocess.run([_JBIG2_BIN, ...])` with `subprocess.run([_resolve_jbig2_bin(), ...])`.

- [ ] **Step 8.2: Same pattern in `pdf_smasher/audit.py`**

In `audit.py`, the `_probe(binary, ...)` helper takes a basename. Change it to resolve via `shutil.which(binary)` first and pass the resolved absolute path to `subprocess.run`. If `which` returns None, return `"?"` (the existing not-found behavior).

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
- Modify: `pdf_smasher/engine/rasterize.py`, `pdf_smasher/engine/image_export.py`, `pdf_smasher/engine/compose.py`, `pdf_smasher/engine/verifier.py` (call `ensure_capped()` at module top)
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

- [ ] **Step 9.4: Call `ensure_capped()` from engine modules**

At the top of each of `rasterize.py`, `image_export.py`, `compose.py`, `verifier.py`, after the imports, add:

```python
from pdf_smasher._pillow_hardening import ensure_capped

ensure_capped()
```

- [ ] **Step 9.5: Run all tests**

```bash
uv run pytest tests/unit -q
```

Expected: all green.

- [ ] **Step 9.6: Commit**

```bash
git add pdf_smasher/_pillow_hardening.py pdf_smasher/engine/rasterize.py pdf_smasher/engine/image_export.py pdf_smasher/engine/compose.py pdf_smasher/engine/verifier.py tests/unit/test_pillow_cap_idempotent.py
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
        uses: pypa/gh-action-pypi-publish@76f52bc884231f62b9a034ebfe128415bbaabdfc  # v1.12.4
```

(Pin the `pypa/gh-action-pypi-publish` SHA to a current release; verify with `gh api repos/pypa/gh-action-pypi-publish/releases/latest --jq .tag_name`.)

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
      label: hankpdf --version output
      description: Paste the full output of `hankpdf --version`.
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
- [ ] Conventional Commits prefix on every commit (feat / fix / chore / docs / refactor / test / perf / ci)

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

- [ ] **Step 14.3: Fix Docker image tag example**

Find the README block that references `ghcr.io/hank-ai/hankpdf:v0.0.1` (around line 109). Change the version tag to `latest` so the example is runnable today regardless of whether a tagged release has been cut.

- [ ] **Step 14.4: Verify**

```bash
grep -n "327 tests\|v0.0.1" README.md
```

Expected: no matches.

- [ ] **Step 14.5: Commit**

```bash
git add README.md
git commit -m "docs(readme): update test count, reconcile GHCR claim, fix docker tag example"
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
  --include="*.md" --include="*.toml" --include="*.py" --include="*.yml" --include="*.json" .
```

Expected: only matches inside `docs/superpowers/specs/` and `.git/`.

- [ ] **Confirm new files exist**

```bash
ls CODE_OF_CONDUCT.md \
   .github/workflows/release.yml \
   .github/PULL_REQUEST_TEMPLATE.md \
   .github/ISSUE_TEMPLATE/bug_report.yml \
   .github/ISSUE_TEMPLATE/feature_request.yml \
   .github/ISSUE_TEMPLATE/config.yml \
   pdf_smasher/engine/_render_safety.py
```

Expected: all listed.

- [ ] **Done**

Hand back to /jack-it-up Phase 5 (`/dc` review of the implementation).
