"""CLI-level integration tests for --output-format jpeg|png|webp."""

from __future__ import annotations

import io

import pikepdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from pdf_smasher.cli.main import main


def _make_pdf(tmp_path, n_pages: int = 2):  # type: ignore[no-untyped-def]
    """Build an N-page white PDF at tmp_path/in.pdf."""
    pdf = pikepdf.new()
    for _ in range(n_pages):
        img = Image.new("RGB", (850, 1100), color="white")
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("Arial.ttf", 40)
        except OSError:
            font = ImageFont.load_default(size=40)
        draw.text((100, 500), "X", fill="black", font=font)
        pdf.add_blank_page(page_size=(612, 792))
        page = pdf.pages[-1]
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        xobj = pdf.make_stream(
            buf.getvalue(),
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Image,
            Width=img.width,
            Height=img.height,
            ColorSpace=pikepdf.Name.DeviceRGB,
            BitsPerComponent=8,
            Filter=pikepdf.Name.DCTDecode,
        )
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Scan=xobj))
        page.Contents = pdf.make_stream(b"q 612 0 0 792 0 0 cm /Scan Do Q\n")
    path = tmp_path / "in.pdf"
    pdf.save(path)
    return path


@pytest.mark.integration
def test_image_export_refuses_encrypted_pdf(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Image-export must enforce the same gates as compress(): an encrypted
    PDF without a password must be refused with EXIT_ENCRYPTED (10).
    Regression gate: DCR Wave 1 found image-export bypassed this check."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    in_path = tmp_path / "enc.pdf"
    pdf.save(in_path, encryption=pikepdf.Encryption(user="secret", owner="o"))
    out_path = tmp_path / "out.jpg"
    rc = main([str(in_path), "-o", str(out_path)])
    assert rc == 10, f"expected EXIT_ENCRYPTED=10, got {rc}"
    assert not out_path.exists()


@pytest.mark.integration
def test_image_export_refuses_signed_pdf(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Signed PDFs must be refused in image-export too (EXIT_SIGNED=11)."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.Root["/AcroForm"] = pikepdf.Dictionary(
        SigFlags=3, Fields=pikepdf.Array([]),
    )
    in_path = tmp_path / "signed.pdf"
    pdf.save(in_path)
    out_path = tmp_path / "out.jpg"
    rc = main([str(in_path), "-o", str(out_path)])
    assert rc == 11, f"expected EXIT_SIGNED=11, got {rc}"
    assert not out_path.exists()


@pytest.mark.integration
def test_image_export_empty_pages_spec_exits_usage_error(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    """Empty --pages string must return EXIT_USAGE (40), not silently exit 0.
    Regression gate: DCR Wave 1 flagged that env-var-expansion producing an
    empty string used to silently succeed with zero files written."""
    in_path = _make_pdf(tmp_path, n_pages=2)
    out_path = tmp_path / "out.jpg"
    rc = main([str(in_path), "-o", str(out_path), "--pages", ""])
    assert rc == 40, f"expected EXIT_USAGE=40, got {rc}"
    # No file should be written.
    assert not out_path.exists(), f"unexpected file written: {out_path}"
    err = capsys.readouterr().err
    assert "--pages" in err, f"expected '--pages' in stderr; got: {err!r}"
    assert "empty" in err.lower() or "no pages" in err.lower(), (
        f"expected 'empty' or 'no pages' in stderr; got: {err!r}"
    )


@pytest.mark.integration
def test_image_export_rejects_excessive_dpi(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """--image-dpi 5000 on a standard letter page would produce a
    42000x54000 RGB buffer (~6 GB). Refuse before allocating.

    argparse calls sys.exit(2) when a type validator raises
    ArgumentTypeError, so we expect SystemExit with code 2.
    """
    in_path = _make_pdf(tmp_path, n_pages=1)
    out_path = tmp_path / "out.jpg"
    with pytest.raises(SystemExit) as exc_info:
        main([str(in_path), "-o", str(out_path), "--image-dpi", "5000"])
    assert exc_info.value.code == 2, (
        f"expected SystemExit(2) from argparse, got {exc_info.value.code}"
    )


@pytest.mark.integration
def test_image_export_warns_on_max_output_mb(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    """--max-output-mb is a PDF-only concept. In image-export mode it
    used to be silently ignored. DCR Wave 1: emit an explicit stderr
    warning so the user knows their cap isn't honored."""
    in_path = _make_pdf(tmp_path, n_pages=1)
    out_path = tmp_path / "out.jpg"
    rc = main([
        str(in_path),
        "-o", str(out_path),
        "--max-output-mb", "5",
    ])
    assert rc == 0
    err = capsys.readouterr().err
    assert "--max-output-mb" in err, (
        f"expected '--max-output-mb' in stderr; got: {err!r}"
    )
    assert "image" in err.lower(), (
        f"expected 'image' in stderr warning; got: {err!r}"
    )
