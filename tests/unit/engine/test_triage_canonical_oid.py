"""Regression: cycle-detection must not collapse direct sibling dicts.

pikepdf reports ``objgen=(0, 0)`` for every direct (inline) Dictionary
or Array, regardless of position in the tree. An earlier version of
:func:`pdf_smasher.engine.triage._canonical_oid` hashed objgen for all
objects with ``objgen is not None``, which collapsed every direct dict
to the same identity — letting a malicious PDF hide a JS or
EmbeddedFiles entry behind any benign sibling because the second
sibling was treated as already visited.

These tests build a synthetic PDF with two sibling direct dicts where
only one carries a JS payload and assert detection still fires.
"""

from __future__ import annotations

import io

import pikepdf

from pdf_smasher.engine.triage import _canonical_oid, _detect_javascript


def test_canonical_oid_direct_sibling_dicts_have_distinct_ids() -> None:
    a = pikepdf.Dictionary(A=pikepdf.Name.X)
    b = pikepdf.Dictionary(B=pikepdf.Name.Y)
    assert _canonical_oid(a) != _canonical_oid(b), (
        "Direct dicts with objgen=(0,0) must fall back to id() and not collide"
    )


def test_detect_javascript_finds_js_behind_a_benign_sibling_direct_dict() -> None:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    benign = pikepdf.Dictionary(Type=pikepdf.Name.Action)
    evil = pikepdf.Dictionary(JS=pikepdf.String("app.alert('pwned');"))
    pdf.Root["/Probe"] = pikepdf.Dictionary(
        Kids=pikepdf.Array([benign, evil]),
    )
    buf = io.BytesIO()
    pdf.save(buf)
    with pikepdf.open(io.BytesIO(buf.getvalue())) as reopened:
        assert _detect_javascript(reopened) is True, (
            "JS hidden behind a benign sibling direct dict must still be detected"
        )
