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


# ---------- min_ratio (W3-13b) ----------


@pytest.mark.integration
def test_min_ratio_passthrough_when_ratio_below_floor() -> None:
    """If the realized compression ratio is below ``min_ratio``, return
    the input unchanged rather than a larger-than-input output.

    A blank PDF compresses poorly (often bigger after our MRC framing)
    — realized ratio ~0.02x. min_ratio=50.0 forces passthrough.
    """
    from pdf_smasher import CompressOptions, compress

    pdf = _make_pdf(1)
    opts = CompressOptions(
        min_ratio=50.0,
        skip_verify=True,
        accept_drift=True,
    )
    out, report = compress(pdf, options=opts)
    assert out == pdf, "below-floor ratio must passthrough"
    assert report.status == "passed_through", (
        f"expected passed_through; got {report.status}"
    )
    assert "passthrough-ratio-floor" in report.warnings


@pytest.mark.integration
def test_min_ratio_default_does_not_force_passthrough_on_good_compression() -> None:
    """Default min_ratio=1.5 must not trigger passthrough when realized
    ratio is higher. (On a blank PDF the ratio is <1; so just assert the
    status/warning combo is correct when the floor is set below realized.)
    """
    from pdf_smasher import CompressOptions, compress

    pdf = _make_pdf(1)
    # Use min_ratio=0.0 to disable the gate entirely — realized ratio on
    # a blank PDF is ~0.02x, which is below the default 1.5.
    opts = CompressOptions(min_ratio=0.0, skip_verify=True, accept_drift=True)
    _out, report = compress(pdf, options=opts)
    assert report.status != "passed_through"
    assert "passthrough-ratio-floor" not in report.warnings


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
    # Also disable the min_ratio floor so the blank-PDF ratio check
    # (which would trigger on a blank page) doesn't mask the min_input_mb
    # behavior we're testing here.
    opts = CompressOptions(
        min_input_mb=0.0,
        min_ratio=0.0,
        skip_verify=True,
        accept_drift=True,
    )
    _out, report = compress(pdf, options=opts)
    assert report.status != "passed_through", (
        "min_input_mb=0 must not trigger passthrough"
    )
    assert "passthrough-min-input-mb" not in report.warnings
