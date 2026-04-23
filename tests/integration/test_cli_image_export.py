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
