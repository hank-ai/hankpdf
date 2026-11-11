"""Open a pdfium document from bytes and release the bytes when the work is done.

Use :func:`open_document` for every pdfium document that the engine opens
from a ``bytes`` object. Do not call ``pypdfium2.PdfDocument(data)`` directly.

Why: ``pypdfium2.PdfDocument`` keeps its input on the object (``_input``),
and the object is part of a reference cycle. ``close()`` frees the pdfium
handle, but the input bytes stay alive until the Python cycle collector
runs. The collector counts objects, not bytes. A loop that reads a large
file again for each call creates few objects, so the collector does not
run, and each pass keeps one more full copy of the file. A loop that passes
the same ``bytes`` object each time does not grow: the references all point
at one buffer.

Measured 2026-11-11 with pypdfium2 5.7.1 on a 327 MB file, read again for
each call: the memory footprint grew by about one file size per call
(5.0 GB after 16 calls). With the release below it stays at one file size.
A measurement script that rendered each page of a 775 MB file this way took
a 32 GB machine down twice.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pypdfium2 as pdfium


def release_input(pdf: pdfium.PdfDocument) -> None:
    """Drop the references that a CLOSED document still holds to its input.

    Call it only after ``pdf.close()``: pdfium reads from the buffer while
    the document is open. ``_input`` and ``_data_holder`` are private
    attributes of pypdfium2. ``tests/unit/engine/test_pdfium_release.py``
    fails when a new version stops keeping the input there, so a rename
    cannot bring the retention back unseen.
    """
    if getattr(pdf, "_input", None) is not None:
        pdf._input = None  # noqa: SLF001 - see the docstring
    holder = getattr(pdf, "_data_holder", None)
    if isinstance(holder, list):
        holder.clear()


@contextmanager
def open_document(data: bytes, *, password: str | None = None) -> Iterator[pdfium.PdfDocument]:
    """Open ``data`` as a pdfium document; close it and release ``data`` on exit.

    Pages, text pages and bitmaps that the caller takes from the document
    are closed with it (pypdfium2 closes the children of a document). Copy
    what you need out of them before the block ends.
    """
    pdf = pdfium.PdfDocument(data, password=password)
    try:
        yield pdf
    finally:
        pdf.close()
        release_input(pdf)
