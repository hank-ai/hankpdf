"""Pytest configuration + cross-test helpers."""

from __future__ import annotations


def minimal_pdf_bytes() -> bytes:
    """Module-level helper (importable from tests). Use directly OR via the
    `minimal_pdf_bytes_fixture` pytest fixture below.
    """
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
        b"xref\n0 3\n0000000000 65535 f\n0000000009 00000 n\n0000000056 00000 n\n"
        b"trailer<</Size 3/Root 1 0 R>>\n"
        b"startxref\n107\n%%EOF\n"
    )


import pytest  # noqa: E402


@pytest.fixture(scope="session")
def minimal_pdf_bytes_fixture() -> bytes:
    return minimal_pdf_bytes()
