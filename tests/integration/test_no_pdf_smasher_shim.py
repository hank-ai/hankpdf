"""After v0.3.0 the legacy pdf_smasher package no longer exists."""

from __future__ import annotations

import importlib

import pytest


def test_pdf_smasher_module_no_longer_importable() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("pdf_smasher")


def test_canonical_import_still_works() -> None:
    from hankpdf import CompressOptions, compress, compress_stream  # noqa: F401
