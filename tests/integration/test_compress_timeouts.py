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
    """Create a multi-page PDF whose pages are MRC-worthy (image XObject
    attached) so the per-page gate doesn't short-circuit to whole-doc
    passthrough — these tests exercise the pipeline below the gate.
    """
    pdf = pikepdf.new()
    for _ in range(n):
        page = pdf.add_blank_page(page_size=(612, 792))
        img = pdf.make_stream(
            b"\x00" * 2048,
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Image,
            Width=10,
            Height=10,
            BitsPerComponent=8,
            ColorSpace=pikepdf.Name.DeviceRGB,
            Filter=pikepdf.Name.FlateDecode,
        )
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=img))
        page.Contents = pdf.make_stream(b"q 612 0 0 792 0 0 cm /Im0 Do Q\n")
    if with_payload:
        # Stuff a dummy stream so the PDF isn't trivially small.
        _ = pdf.make_stream(b"X" * 1024, Type=pikepdf.Name.Metadata)
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


# ---------- total_timeout_seconds (W3-13d) ----------


@pytest.mark.integration
def test_total_timeout_fires_on_long_run() -> None:
    """A cumulative wall-clock > total_timeout_seconds must raise
    ``TotalTimeoutError``. Uses a tiny budget and a slow mocked worker
    so the watchdog trips during the merge phase.
    """
    import time as _time
    from unittest.mock import patch

    from pdf_smasher import CompressOptions, compress
    from pdf_smasher.exceptions import TotalTimeoutError

    pdf = _make_pdf(2)

    import pdf_smasher as _pkg

    _real = _pkg._process_single_page  # noqa: SLF001 — accessing to re-dispatch in test

    def _slow_worker(w: object) -> object:
        # Call the real implementation AFTER sleeping to eat the budget.
        _time.sleep(0.6)
        return _real(w)  # type: ignore[arg-type]

    opts = CompressOptions(
        max_workers=1,
        per_page_timeout_seconds=120,  # don't let per-page fire first
        total_timeout_seconds=1,  # 0.6s sleep x 2 pages ≈ 1.2s > 1s budget
        skip_verify=True,
        accept_drift=True,
        min_ratio=0.0,
    )
    with (
        patch.object(_pkg, "_process_single_page", _slow_worker),
        pytest.raises(TotalTimeoutError),
    ):
        compress(pdf, options=opts)


@pytest.mark.integration
def test_total_timeout_zero_disables_watchdog() -> None:
    """total_timeout_seconds=0 disables the watchdog entirely."""
    from pdf_smasher import CompressOptions, compress

    pdf = _make_pdf(1)
    opts = CompressOptions(
        total_timeout_seconds=0,
        skip_verify=True,
        accept_drift=True,
        min_ratio=0.0,
    )
    # Must not raise — the default would have been fine anyway on this
    # tiny input, but a zero budget must not itself cause an immediate
    # timeout.
    _out, report = compress(pdf, options=opts)
    assert report.status in {"ok", "passed_through"}


# ---------- per_page_timeout_seconds (W3-13c) ----------


@pytest.mark.integration
def test_per_page_timeout_raises_typed_error() -> None:
    """A worker that exceeds ``per_page_timeout_seconds`` must surface
    as ``PerPageTimeoutError``, not a generic ``CompressError``.

    Exercise via the serial path with max_workers=1 — the parallel path
    uses the same ``future.result(timeout=)`` guard but the serial path
    needs equivalent protection too (see implementation).
    """
    import time as _time
    from unittest.mock import patch

    from pdf_smasher import CompressOptions, compress
    from pdf_smasher.exceptions import PerPageTimeoutError

    pdf = _make_pdf(1)

    # Monkeypatch _process_single_page to sleep longer than the budget.
    # This exercises the wrapper's timeout path without actually wedging
    # a Tesseract subprocess.
    import pdf_smasher as _pkg

    _unreachable_msg = "should never reach here"

    def _slow_worker(_w: object) -> object:
        _time.sleep(2)
        raise AssertionError(_unreachable_msg)

    opts = CompressOptions(
        max_workers=1,
        per_page_timeout_seconds=1,  # 1 second — the sleep is 2 seconds
        skip_verify=True,
        accept_drift=True,
        min_ratio=0.0,  # disable the ratio gate so we reach the per-page loop
    )
    with (
        patch.object(_pkg, "_process_single_page", _slow_worker),
        pytest.raises(PerPageTimeoutError),
    ):
        compress(pdf, options=opts)


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
    assert report.status == "passed_through", f"expected passed_through; got {report.status}"
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
    assert report.status == "passed_through", f"expected status=passed_through; got {report.status}"
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
    assert report.status != "passed_through", "min_input_mb=0 must not trigger passthrough"
    assert "passthrough-min-input-mb" not in report.warnings
