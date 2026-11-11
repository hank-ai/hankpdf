"""A document opened from bytes must not keep the bytes after the work is done.

``pypdfium2.PdfDocument`` keeps its input on the object, and the object is
part of a reference cycle, so a closed document pins its whole input until
the cycle collector runs. These tests turn the collector off: with the
release in ``hankpdf.engine._pdfium`` the memory stays flat; without it,
each call keeps one more copy of the document (12 calls on an 8 MB file is
about 96 MB).
"""

from __future__ import annotations

import gc
import io
import secrets
import tracemalloc
from collections.abc import Callable, Iterator

import pikepdf
import pypdfium2 as pdfium
import pytest

from hankpdf.engine import image_export, text_layer
from hankpdf.engine._pdfium import open_document, release_input
from hankpdf.engine.rasterize import rasterize_page

_PAYLOAD_BYTES = 8 * 1024 * 1024
_CALLS = 12


@pytest.fixture(scope="module")
def big_pdf() -> bytes:
    """A two-page PDF that carries 8 MB of data that Flate cannot shrink."""
    pdf = pikepdf.new()
    for _ in range(2):
        pdf.add_blank_page(page_size=(200, 200))
    pdf.pages[0]["/UniquePayload"] = pdf.make_stream(secrets.token_bytes(_PAYLOAD_BYTES))
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


@pytest.fixture
def no_cycle_collector() -> Iterator[None]:
    """Run the test with the cycle collector off, so only real releases free memory."""
    gc.collect()
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()
        gc.collect()


def _retained_after(calls: int, work: Callable[[bytes], object], data: bytes) -> int:
    """Return the traced bytes still alive after ``calls`` runs of ``work`` on fresh copies."""
    tracemalloc.start()
    try:
        before, _ = tracemalloc.get_traced_memory()
        for _ in range(calls):
            copy = bytes(bytearray(data))  # a new bytes object each time, as a caller would read it
            work(copy)
            del copy
        after, _ = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return after - before


def test_bare_pdfdocument_still_pins_its_input(big_pdf: bytes, no_cycle_collector: None) -> None:
    """The library behaviour this module works around. If this fails, pypdfium2 changed:
    read ``hankpdf/engine/_pdfium.py`` again and check that the release is still right."""

    def bare(data: bytes) -> None:
        pdf = pdfium.PdfDocument(data)
        pdf.close()

    assert _retained_after(_CALLS, bare, big_pdf) > (_CALLS - 2) * len(big_pdf)


def test_release_input_frees_a_closed_document(big_pdf: bytes, no_cycle_collector: None) -> None:
    def released(data: bytes) -> None:
        pdf = pdfium.PdfDocument(data)
        pdf.close()
        release_input(pdf)

    assert _retained_after(_CALLS, released, big_pdf) < len(big_pdf)


def test_open_document_releases_on_exit_and_on_error(
    big_pdf: bytes, no_cycle_collector: None
) -> None:
    def normal(data: bytes) -> None:
        with open_document(data) as pdf:
            assert len(pdf) == 2

    def use_then_fail(data: bytes) -> None:
        msg = "boom"
        with open_document(data) as pdf:
            assert len(pdf) == 2
            raise RuntimeError(msg)

    def failing(data: bytes) -> None:
        with pytest.raises(RuntimeError, match="boom"):
            use_then_fail(data)

    assert _retained_after(_CALLS, normal, big_pdf) < len(big_pdf)
    assert _retained_after(_CALLS, failing, big_pdf) < len(big_pdf)


def test_rasterize_page_does_not_keep_the_document(
    big_pdf: bytes, no_cycle_collector: None
) -> None:
    """One call for each page of a large file must not keep one copy of the file per call."""

    def render(data: bytes) -> None:
        image = rasterize_page(data, page_index=1, dpi=36)
        assert image.size == (100, 100)

    assert _retained_after(_CALLS, render, big_pdf) < len(big_pdf)


def test_native_text_extraction_does_not_keep_the_document(
    big_pdf: bytes, no_cycle_collector: None
) -> None:
    def words(data: bytes) -> None:
        text_layer.extract_native_word_boxes(
            data, page_index=1, raster_width_px=100, raster_height_px=100
        )

    assert _retained_after(_CALLS, words, big_pdf) < len(big_pdf)


def test_image_export_page_size_does_not_keep_the_document(
    big_pdf: bytes, no_cycle_collector: None
) -> None:
    def size(data: bytes) -> None:
        assert image_export._page_size_points(data, 1) == (200.0, 200.0)

    assert _retained_after(_CALLS, size, big_pdf) < len(big_pdf)
