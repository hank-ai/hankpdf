"""Regression: cycle-detection must not collapse direct sibling dicts.

pikepdf reports ``objgen=(0, 0)`` for every direct (inline) Dictionary
or Array, regardless of position in the tree. Two earlier versions of
:func:`hankpdf.engine.triage._canonical_oid` had distinct bugs that
both let a malicious PDF hide a JS or EmbeddedFiles entry behind a
benign sibling:

1. Hashing ``objgen`` unconditionally collapsed every direct dict to
   one identity, marking the second sibling as already visited.
2. Falling back to ``id()`` on the Python wrapper looked correct in
   isolation, but pikepdf creates fresh wrappers per access and
   Python's allocator recycles addresses of recently-GC'd wrappers —
   so two distinct direct siblings could collide intermittently
   (observed as macOS/Windows CI flakes on this regression test).

Current implementation: :func:`_canonical_oid` returns ``None`` for
direct objects. The walker treats ``None`` as "do not dedupe", which
is correct because direct objects cannot form cycles in a well-formed
PDF (cycles require indirect references). The depth cap bounds
adversarial input.
"""

from __future__ import annotations

import io

import pikepdf

from hankpdf.engine.triage import _canonical_oid, _detect_javascript


def test_canonical_oid_returns_none_for_direct_objects() -> None:
    """Direct dicts must signal "don't dedupe" via ``None`` so the walker
    recurses into every sibling regardless of wrapper-allocator quirks.
    """
    a = pikepdf.Dictionary(A=pikepdf.Name.X)
    b = pikepdf.Dictionary(B=pikepdf.Name.Y)
    assert _canonical_oid(a) is None
    assert _canonical_oid(b) is None


def test_detect_javascript_finds_js_behind_a_benign_sibling_direct_dict() -> None:
    """JS hidden behind a benign sibling direct dict must still be detected.

    Run multiple iterations in a single test to catch the GC-recycle
    flake mode: under the old ``id()``-based fallback, ~30% of macOS/
    Windows runs would mis-mark the evil sibling as already-visited
    when Python recycled the benign wrapper's address.
    """
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    benign = pikepdf.Dictionary(Type=pikepdf.Name.Action)
    evil = pikepdf.Dictionary(JS=pikepdf.String("app.alert('pwned');"))
    pdf.Root["/Probe"] = pikepdf.Dictionary(
        Kids=pikepdf.Array([benign, evil]),
    )
    buf = io.BytesIO()
    pdf.save(buf)
    saved = buf.getvalue()
    # 50 iterations with explicit reopens to maximize GC churn — under
    # the previous id()-fallback, this would have failed at least once.
    for _ in range(50):
        with pikepdf.open(io.BytesIO(saved)) as reopened:
            assert _detect_javascript(reopened) is True, (
                "JS hidden behind a benign sibling direct dict must still be detected"
            )
