"""Verify --password-file plumbs through to every pikepdf-open site."""

from __future__ import annotations

import io

import pikepdf

from hankpdf import triage


def _make_encrypted_pdf(password: str, *, pages: int = 1) -> bytes:
    pdf = pikepdf.new()
    for _ in range(pages):
        pdf.add_blank_page(page_size=(612, 792))
    buf = io.BytesIO()
    pdf.save(buf, encryption=pikepdf.Encryption(owner=password, user=password, R=6))
    return buf.getvalue()


def test_triage_with_correct_password_succeeds() -> None:
    pdf_bytes = _make_encrypted_pdf("hunter2")
    report = triage(pdf_bytes, password="hunter2")
    assert report.classification != "require-password"
    assert report.is_encrypted is True


def test_triage_with_wrong_password_classifies_require_password() -> None:
    pdf_bytes = _make_encrypted_pdf("hunter2")
    report = triage(pdf_bytes, password="wrong")
    assert report.classification == "require-password"


def test_triage_with_no_password_classifies_require_password() -> None:
    # Regression coverage: existing behavior still works.
    pdf_bytes = _make_encrypted_pdf("hunter2")
    report = triage(pdf_bytes)
    assert report.classification == "require-password"


def test_triage_multipage_with_correct_password_returns_correct_page_count() -> None:
    pdf_bytes = _make_encrypted_pdf("hunter2", pages=5)
    report = triage(pdf_bytes, password="hunter2")
    assert report.classification != "require-password"
    assert report.pages == 5


def test_canonical_hash_with_correct_password_succeeds() -> None:
    """Forwarder: canonical_input_sha256 uses the password to open."""
    from hankpdf.engine.canonical import canonical_input_sha256

    pdf_bytes = _make_encrypted_pdf("hunter2")
    digest = canonical_input_sha256(pdf_bytes, password="hunter2")
    assert isinstance(digest, str)
    assert len(digest) == 64


def test_image_export_with_correct_password_succeeds() -> None:
    """Image-export route: encrypted input + correct password → JPEG bytes out.

    Regression guard for the rasterize_page / _iter_pages_impl password
    threading. Without password threading on the image-export route,
    this fails inside _page_size_points or rasterize_page on the
    encrypted PDF.
    """
    from hankpdf.engine.image_export import iter_pages_as_images

    pdf_bytes = _make_encrypted_pdf("hunter2", pages=2)
    blobs = list(
        iter_pages_as_images(
            pdf_bytes,
            [0, 1],
            image_format="jpeg",
            dpi=72,
            password="hunter2",
        )
    )
    assert len(blobs) == 2
    # JPEG SOI marker
    assert all(b[:3] == b"\xff\xd8\xff" for b in blobs)


def test_compress_with_correct_password_succeeds() -> None:
    """End-to-end: encrypted input + correct password through compress()."""
    from hankpdf import compress
    from hankpdf.types import CompressOptions

    pdf_bytes = _make_encrypted_pdf("hunter2", pages=2)
    options = CompressOptions(password="hunter2", skip_verify=True)
    output, report = compress(pdf_bytes, options=options)
    assert isinstance(output, bytes)
    assert report.input_bytes == len(pdf_bytes)
