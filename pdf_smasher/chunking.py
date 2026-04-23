"""Split a merged PDF into size-bounded chunks.

Supports the ``--max-output-mb`` CLI flag and the library-level
``split_pdf_by_size`` API. The algorithm is greedy: walk pages in their
original order, add each to the current chunk until the next would push
the chunk over the limit, then start a new chunk.

A single page whose serialized size already exceeds the cap is emitted
in its own chunk (we can't split within a page). Callers that care can
compare the returned chunk's len to max_bytes to detect this.
"""

from __future__ import annotations

import io

import pikepdf


def split_pdf_by_size(pdf_bytes: bytes, *, max_bytes: int) -> list[bytes]:
    """Greedy-pack pages into chunks each <= ``max_bytes`` (when possible).

    Parameters
    ----------
    pdf_bytes:
        A single merged PDF.
    max_bytes:
        Per-chunk byte cap. Must be > 0.

    Returns
    -------
    list[bytes]
        One or more PDFs. If the input is already <= ``max_bytes``, returns
        ``[pdf_bytes]`` unchanged. Order of pages across chunks preserves
        the original page order.

    Raises
    ------
    ValueError
        If ``max_bytes`` is not positive.
    """
    if max_bytes <= 0:
        msg = f"max_bytes must be > 0, got {max_bytes}"
        raise ValueError(msg)

    if len(pdf_bytes) <= max_bytes:
        return [pdf_bytes]

    with pikepdf.open(io.BytesIO(pdf_bytes)) as src:
        n_pages = len(src.pages)
        if n_pages <= 1:
            # Can't split within a single page.
            return [pdf_bytes]

        # Measure each page's standalone serialized size. This is a slight
        # over-estimate of the per-page incremental cost in a merged chunk
        # (a multi-page PDF shares its /Catalog and xref), but the delta is
        # tens of bytes per page — negligible vs. a 50 MB target.
        page_costs: list[int] = []
        for i in range(n_pages):
            single = pikepdf.new()
            try:
                single.pages.append(src.pages[i])
                buf = io.BytesIO()
                single.save(buf, linearize=False, deterministic_id=True)
                page_costs.append(len(buf.getvalue()))
            finally:
                single.close()

        # Greedy pack.
        chunks_of_indices: list[list[int]] = []
        current: list[int] = []
        current_size = 0
        for i, cost in enumerate(page_costs):
            if current and current_size + cost > max_bytes:
                chunks_of_indices.append(current)
                current = []
                current_size = 0
            current.append(i)
            current_size += cost
        if current:
            chunks_of_indices.append(current)

        # Materialize each chunk.
        out: list[bytes] = []
        for indices in chunks_of_indices:
            chunk = pikepdf.new()
            try:
                for i in indices:
                    chunk.pages.append(src.pages[i])
                buf = io.BytesIO()
                chunk.save(buf, linearize=False, deterministic_id=True)
                out.append(buf.getvalue())
            finally:
                chunk.close()
        return out
