"""Tests for pdf_smasher.chunking.split_pdf_by_size."""

from __future__ import annotations

import io

import pikepdf
import pypdfium2 as pdfium
import pytest

from pdf_smasher.chunking import split_pdf_by_size


def _make_n_page_pdf(n: int, payload_kb_per_page: int = 0) -> bytes:
    """Build an N-page PDF. Each page:
    - has a unique height (792 + i pt) so page order is verifiable via get_size
    - carries a random-byte blob so the serialized footprint is predictable
      (random resists flate compression so we can hit controllable sizes)
    """
    import secrets

    pdf = pikepdf.new()
    for i in range(n):
        height_pt = 792.0 + i
        pdf.add_blank_page(page_size=(612.0, height_pt))
        page = pdf.pages[-1]
        if payload_kb_per_page:
            filler = secrets.token_bytes(payload_kb_per_page * 1024)
            meta = pdf.make_stream(filler)
            page["/UniquePayload"] = meta
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def test_passthrough_when_under_max() -> None:
    """A PDF already under the limit returns [original_bytes] unchanged."""
    pdf_bytes = _make_n_page_pdf(3)
    chunks = split_pdf_by_size(pdf_bytes, max_bytes=10_000_000)
    assert len(chunks) == 1
    assert chunks[0] == pdf_bytes


def test_splits_multi_page_when_over_max() -> None:
    """A 10-page PDF with 100 KB/page should split into ~4 chunks at 300 KB cap."""
    pdf_bytes = _make_n_page_pdf(10, payload_kb_per_page=100)
    assert len(pdf_bytes) > 300_000

    chunks = split_pdf_by_size(pdf_bytes, max_bytes=300_000)
    assert len(chunks) >= 2
    # Each chunk must be a valid PDF
    total_pages = 0
    for chunk in chunks:
        with pikepdf.open(io.BytesIO(chunk)) as doc:
            total_pages += len(doc.pages)
    # All pages accounted for
    assert total_pages == 10


def test_preserves_page_order_across_chunks() -> None:
    """Chunk N contains pages [a, a+1, ..., b]; chunk N+1 starts at b+1.
    Verified by walking chunks and reading each page's height — _make_n_page_pdf
    assigns 792+i pt to page i, so the concatenated sequence of heights
    across all chunks must equal [792, 793, ..., 801].
    """
    pdf_bytes = _make_n_page_pdf(10, payload_kb_per_page=100)
    chunks = split_pdf_by_size(pdf_bytes, max_bytes=300_000)

    seen_heights: list[int] = []
    for chunk in chunks:
        doc = pdfium.PdfDocument(chunk)
        try:
            for i in range(len(doc)):
                _w, h = doc[i].get_size()
                seen_heights.append(round(h))
        finally:
            doc.close()

    expected = [792 + i for i in range(10)]
    assert seen_heights == expected


def test_single_page_exceeding_max_returns_alone() -> None:
    """A lone page bigger than the cap must still be emitted (warning-worthy
    but correct — we can't split within a page)."""
    pdf_bytes = _make_n_page_pdf(1, payload_kb_per_page=500)
    assert len(pdf_bytes) > 100_000

    chunks = split_pdf_by_size(pdf_bytes, max_bytes=100_000)
    assert len(chunks) == 1
    # The returned chunk is the original (or equivalent); must contain 1 page.
    with pikepdf.open(io.BytesIO(chunks[0])) as doc:
        assert len(doc.pages) == 1


def test_rejects_nonpositive_max_bytes() -> None:
    """max_bytes must be > 0."""
    pdf_bytes = _make_n_page_pdf(2)
    with pytest.raises(ValueError, match="max_bytes"):
        split_pdf_by_size(pdf_bytes, max_bytes=0)
    with pytest.raises(ValueError, match="max_bytes"):
        split_pdf_by_size(pdf_bytes, max_bytes=-1)


def test_every_chunk_under_max_when_possible() -> None:
    """When no single page exceeds max_bytes, every chunk is <= max_bytes."""
    pdf_bytes = _make_n_page_pdf(8, payload_kb_per_page=50)
    max_bytes = 200_000
    chunks = split_pdf_by_size(pdf_bytes, max_bytes=max_bytes)
    for i, chunk in enumerate(chunks):
        assert len(chunk) <= max_bytes, (
            f"chunk {i} size {len(chunk):,} exceeds cap {max_bytes:,}"
        )
