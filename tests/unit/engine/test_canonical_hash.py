"""Tests for canonical-input hashing (SPEC.md §5).

Two content-identical PDFs that differ only in /ID, /CreationDate, etc.
should produce the same hash. Two content-different PDFs must differ.
"""

from __future__ import annotations

import io

import pikepdf

from hankpdf.engine.canonical import canonical_input_sha256


def _pdf_with(*, producer: str = "pdftest") -> bytes:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    if "/Info" not in pdf.trailer:
        pdf.trailer["/Info"] = pikepdf.Dictionary()
    pdf.trailer["/Info"]["/Producer"] = pikepdf.String(producer)
    pdf.trailer["/Info"]["/CreationDate"] = pikepdf.String("D:20250101000000Z")
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def test_canonical_hash_is_hex_64_chars() -> None:
    h = canonical_input_sha256(_pdf_with())
    assert len(h) == 64
    int(h, 16)  # parses as hex


def test_same_content_different_metadata_produces_same_hash() -> None:
    a = _pdf_with(producer="Producer-A")
    b = _pdf_with(producer="Producer-B")
    # Different producer; same content. Canonical hash should match.
    assert canonical_input_sha256(a) == canonical_input_sha256(b)


def test_different_content_produces_different_hash() -> None:
    a = _pdf_with()
    # Second PDF has 2 pages — different structure.
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.add_blank_page(page_size=(612, 792))
    buf = io.BytesIO()
    pdf.save(buf)
    b = buf.getvalue()
    assert canonical_input_sha256(a) != canonical_input_sha256(b)
