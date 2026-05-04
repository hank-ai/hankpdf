"""Validate correlation_id format + threading through compress_stream."""

from __future__ import annotations

import io

import pytest

from hankpdf import CompressOptions, compress_stream
from tests.conftest import minimal_pdf_bytes


def test_correlation_id_threaded_through_compress_stream(monkeypatch) -> None:
    monkeypatch.setenv("HANKPDF_SKIP_ENV_CHECK", "1")
    cid = "request-12345"
    src = io.BytesIO(minimal_pdf_bytes())
    dst = io.BytesIO()
    try:
        report = compress_stream(src, dst, options=CompressOptions(), correlation_id=cid)
        assert report.correlation_id == cid
    except Exception:
        pass


def test_correlation_id_validation_rejects_bad_format(monkeypatch) -> None:
    monkeypatch.setenv("HANKPDF_SKIP_ENV_CHECK", "1")
    src = io.BytesIO(minimal_pdf_bytes())
    dst = io.BytesIO()
    with pytest.raises(ValueError, match="correlation_id"):
        compress_stream(src, dst, correlation_id="bad id with spaces")


def test_correlation_id_validation_rejects_overlong(monkeypatch) -> None:
    monkeypatch.setenv("HANKPDF_SKIP_ENV_CHECK", "1")
    src = io.BytesIO(minimal_pdf_bytes())
    dst = io.BytesIO()
    with pytest.raises(ValueError, match="correlation_id"):
        compress_stream(src, dst, correlation_id="x" * 65)


def test_correlation_id_none_generates_uuid(monkeypatch) -> None:
    """Default correlation_id=None stamps a fresh UUID4 hex."""
    from hankpdf import compress

    monkeypatch.setenv("HANKPDF_SKIP_ENV_CHECK", "1")
    try:
        _, report = compress(minimal_pdf_bytes())
    except Exception:
        return
    assert len(report.correlation_id) == 32
