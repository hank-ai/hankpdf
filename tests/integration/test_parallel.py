"""Parallel-path equivalence: parallel output must match serial byte-for-byte.

Proves:
- parallel dispatch (threads by default, processes via HANKPDF_POOL) produces
  byte-identical output to the serial path
- output page order matches input page order regardless of worker completion
- --max-workers=1 disables the pool entirely
"""

from __future__ import annotations

import io

import numpy as np
import pikepdf
import pypdfium2 as pdfium
import pytest
from PIL import Image

from pdf_smasher import CompressOptions, compress


def _make_multi_page_pdf(n_pages: int = 5) -> bytes:
    """Build an N-page PDF. Each page has a unique HEIGHT (792 + page_index pt)
    so the output page size identifies which source page it came from. Our
    compose_* paths preserve per-page size, so this survives the pipeline.
    """
    pdf = pikepdf.new()
    for page_i in range(n_pages):
        arr = np.full((2200, 1700, 3), 140, dtype=np.uint8)
        arr[300:1900, 200:1500] = 80  # dark band → MIXED routing
        arr[50:100, 50:400] = [200, 40, 40]  # red banner (avoids is_effectively_monochrome)
        img = Image.fromarray(arr)
        page_height_pt = 792.0 + page_i
        pdf.add_blank_page(page_size=(612.0, page_height_pt))
        page = pdf.pages[-1]
        jbuf = io.BytesIO()
        img.save(jbuf, format="JPEG", quality=92, subsampling=0)
        xobj = pdf.make_stream(
            jbuf.getvalue(),
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Image,
            Width=img.width,
            Height=img.height,
            ColorSpace=pikepdf.Name.DeviceRGB,
            BitsPerComponent=8,
            Filter=pikepdf.Name.DCTDecode,
        )
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Scan=xobj))
        page.Contents = pdf.make_stream(
            f"q 612 0 0 {page_height_pt} 0 0 cm /Scan Do Q\n".encode("ascii"),
        )
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


@pytest.mark.integration
def test_parallel_and_serial_produce_equivalent_output() -> None:
    """Serial (max_workers=1) and parallel (max_workers=0=auto) must produce
    byte-identical output PDFs. pikepdf writes with deterministic_id=True,
    so identical input + identical options should give identical bytes
    regardless of whether a process pool ran the pages.
    """
    pdf_in = _make_multi_page_pdf(n_pages=5)

    serial_bytes, serial_report = compress(
        pdf_in,
        options=CompressOptions(mode="fast", max_workers=1),
    )
    parallel_bytes, parallel_report = compress(
        pdf_in,
        options=CompressOptions(mode="fast", max_workers=0),  # auto
    )

    assert serial_report.pages == parallel_report.pages == 5
    assert serial_report.status == parallel_report.status == "ok"
    assert serial_bytes == parallel_bytes, (
        f"parallel output diverged from serial: "
        f"serial={len(serial_bytes):,} bytes, parallel={len(parallel_bytes):,} bytes"
    )


@pytest.mark.integration
def test_process_pool_path_also_equivalent(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """HANKPDF_POOL=process routes through ProcessPoolExecutor; output must
    still be byte-identical to the serial and thread-pool paths.
    """
    pdf_in = _make_multi_page_pdf(n_pages=5)
    serial_bytes, _ = compress(
        pdf_in, options=CompressOptions(mode="fast", max_workers=1),
    )
    monkeypatch.setenv("HANKPDF_POOL", "process")
    proc_bytes, proc_report = compress(
        pdf_in, options=CompressOptions(mode="fast", max_workers=0),
    )
    assert proc_report.status == "ok"
    assert proc_report.pages == 5
    assert proc_bytes == serial_bytes, (
        f"process-pool output diverged from serial: "
        f"serial={len(serial_bytes):,} bytes, proc={len(proc_bytes):,} bytes"
    )


@pytest.mark.integration
def test_parallel_preserves_page_order() -> None:
    """Output page order must match input page order even when workers
    finish out of order. Force N workers >= N pages so scheduling is
    unpredictable. Each input page has a unique height; the compose
    pipeline preserves per-page dimensions, so the output page size
    at index i should equal the input page size at index i.
    """
    pdf_in = _make_multi_page_pdf(n_pages=5)
    pdf_out, report = compress(
        pdf_in,
        options=CompressOptions(mode="fast", max_workers=8),
    )
    assert report.pages == 5
    doc = pdfium.PdfDocument(pdf_out)
    try:
        assert len(doc) == 5
        for expected_i in range(5):
            _w, h = doc[expected_i].get_size()
            expected_h = 792.0 + expected_i
            assert abs(h - expected_h) < 0.5, (
                f"output page at index {expected_i} has height {h:.2f} but "
                f"should be {expected_h} (source page {expected_i}'s height); "
                "pages are out of order after parallel compression"
            )
    finally:
        doc.close()
