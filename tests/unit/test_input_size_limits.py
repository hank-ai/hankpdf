"""Tighter defaults + stat-before-read on input cap."""

from __future__ import annotations

from pdf_smasher.types import CompressOptions


def test_default_max_input_mb_is_250() -> None:
    assert CompressOptions().max_input_mb == 250.0


def test_default_max_pages_is_10000() -> None:
    assert CompressOptions().max_pages == 10000


def test_cli_default_max_input_mb_is_250() -> None:
    from pdf_smasher.cli.main import _parser

    parser = _parser()
    ns = parser.parse_args(["dummy.pdf", "-o", "out.pdf"])
    assert ns.max_input_mb == 250.0


def test_cli_default_max_pages_is_10000() -> None:
    from pdf_smasher.cli.main import _parser

    parser = _parser()
    ns = parser.parse_args(["dummy.pdf", "-o", "out.pdf"])
    assert ns.max_pages == 10000


def test_cli_default_per_page_min_image_fraction_is_30_percent() -> None:
    from pdf_smasher.cli.main import _parser

    parser = _parser()
    ns = parser.parse_args(["dummy.pdf", "-o", "out.pdf"])
    assert ns.per_page_min_image_fraction == 0.30
