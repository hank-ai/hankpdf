"""Regression: CompressOptions timeout + passthrough thresholds are honored.

Reviewer (pre-mortem scenario 3): CompressOptions advertises
``min_input_mb``, ``min_ratio``, ``per_page_timeout_seconds``, and
``total_timeout_seconds``; none were consulted. THREAT_MODEL.md §1
claims they exist. Now they do.
"""

from __future__ import annotations

import io

import pikepdf
import pytest


def _make_pdf(n: int = 1, with_payload: bool = False) -> bytes:
    pdf = pikepdf.new()
    for _ in range(n):
        pdf.add_blank_page(page_size=(612, 792))
    if with_payload:
        # Stuff a dummy stream so the PDF isn't trivially small.
        _ = pdf.make_stream(b"X" * 1024, Type=pikepdf.Name.Metadata)
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


# ---------- min_input_mb (W3-13a) ----------


@pytest.mark.integration
def test_min_input_mb_below_floor_passthrough() -> None:
    """Input under ``min_input_mb`` must passthrough unchanged."""
    from pdf_smasher import CompressOptions, compress

    pdf = _make_pdf(1)
    # 100KB input vs 10MB floor → must passthrough.
    opts = CompressOptions(min_input_mb=10.0, skip_verify=True)
    out, report = compress(pdf, options=opts)
    assert out == pdf, "passthrough must return input bytes unchanged"
    assert report.status == "passed_through", (
        f"expected status=passed_through; got {report.status}"
    )
    assert "passthrough-min-input-mb" in report.warnings, (
        f"expected passthrough-min-input-mb warning; got {report.warnings}"
    )


@pytest.mark.integration
def test_min_input_mb_at_zero_disables_floor() -> None:
    """Default min_input_mb=0 must not trigger passthrough."""
    from pdf_smasher import CompressOptions, compress

    pdf = _make_pdf(1)
    opts = CompressOptions(min_input_mb=0.0, skip_verify=True, accept_drift=True)
    _out, report = compress(pdf, options=opts)
    assert report.status != "passed_through", (
        "min_input_mb=0 must not trigger passthrough"
    )
    assert "passthrough-min-input-mb" not in report.warnings
