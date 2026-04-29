"""End-to-end CLI tests: run hankpdf against real fixtures."""

from __future__ import annotations

import io
import json

import pikepdf
import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageDraw, ImageFont

from hankpdf.cli.main import main


def _make_pdf(tmp_path, text: str = "HELLO WORLD") -> object:  # type: ignore[no-untyped-def]
    img = Image.new("RGB", (1700, 2200), color="white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("Arial.ttf", 60)
    except OSError:
        font = ImageFont.load_default(size=60)
    draw.text((200, 400), text, fill="black", font=font)

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    jbuf = io.BytesIO()
    img.save(jbuf, format="JPEG", quality=92, subsampling=0)
    xobj = pdf.make_stream(
        jbuf.getvalue(),
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
def test_cli_end_to_end_happy_path(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    in_path = _make_pdf(tmp_path, text="SEARCHABLE TEXT")
    out_path = tmp_path / "out.pdf"
    # --ocr: searchable text layer is opt-in (defaults off for speed).
    rc = main([str(in_path), "-o", str(out_path), "--ocr"])
    assert rc == 0
    assert out_path.exists()
    assert out_path.stat().st_size > 0

    # Output is a valid PDF and text is searchable.
    pdf = pdfium.PdfDocument(out_path.read_bytes())
    try:
        tp = pdf[0].get_textpage()
        text = tp.get_text_bounded()
        tp.close()
    finally:
        pdf.close()
    assert "SEARCHABLE" in text


@pytest.mark.integration
def test_cli_json_report(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    in_path = _make_pdf(tmp_path)
    out_path = tmp_path / "out.pdf"
    rc = main([str(in_path), "-o", str(out_path), "--report", "json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "ratio" in data
    assert "input_bytes" in data
    assert data["pages"] == 1


@pytest.mark.integration
def test_cli_encrypted_input_exits_10(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    in_path = tmp_path / "enc.pdf"
    pdf.save(in_path, encryption=pikepdf.Encryption(user="secret", owner="o"))
    out_path = tmp_path / "out.pdf"
    rc = main([str(in_path), "-o", str(out_path)])
    assert rc == 10
    assert not out_path.exists()


@pytest.mark.integration
def test_cli_signed_input_exits_11(tmp_path) -> None:  # type: ignore[no-untyped-def]
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.Root["/AcroForm"] = pikepdf.Dictionary(SigFlags=3, Fields=pikepdf.Array([]))
    in_path = tmp_path / "signed.pdf"
    pdf.save(in_path)
    out_path = tmp_path / "out.pdf"
    rc = main([str(in_path), "-o", str(out_path)])
    assert rc == 11


def _make_mixed_pdf(tmp_path) -> object:  # type: ignore[no-untyped-def]
    """Build a PDF with a MIXED-route page: noisy gray bg + dark content.
    Needed because a plain white-bg text fixture routes TEXT_ONLY already,
    making --force-monochrome a no-op on size.
    """
    import numpy as np

    arr = np.full((2200, 1700, 3), 140, dtype=np.uint8)
    arr[300:1900, 200:1500] = 80  # dark band → MIXED routing
    arr[50:100, 50:400] = [200, 40, 40]  # red banner → not effectively monochrome
    img = Image.fromarray(arr)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("Arial.ttf", 60)
    except OSError:
        font = ImageFont.load_default(size=60)
    draw.text((300, 500), "MIXED MONO TEST", fill="black", font=font)

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    jbuf = io.BytesIO()
    img.save(jbuf, format="JPEG", quality=92, subsampling=0)
    xobj = pdf.make_stream(
        jbuf.getvalue(),
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
    path = tmp_path / "in-mixed.pdf"
    pdf.save(path)
    return path


@pytest.mark.integration
def test_cli_force_monochrome_flag_routes_through_options(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """--force-monochrome on the CLI must reach CompressOptions and cause the
    MRC background to be encoded in DeviceGray (not DeviceRGB). On already-gray
    fixtures the bytes may be identical; the colorspace change is the gate.
    """
    in_path = _make_mixed_pdf(tmp_path)
    out_default = tmp_path / "default.pdf"
    out_mono = tmp_path / "mono.pdf"

    rc1 = main([str(in_path), "-o", str(out_default), "--mode", "fast"])
    assert rc1 == 0
    rc2 = main([str(in_path), "-o", str(out_mono), "--mode", "fast", "--force-monochrome"])
    assert rc2 == 0

    # Inspect /BG colorspace in each output
    def _bg_colorspace(p) -> str:  # type: ignore[no-untyped-def]
        with pikepdf.open(p) as pdf:
            xobj = pdf.pages[0].Resources.XObject
            # Find whichever key is the bg image (MRC: /BG; PHOTO_ONLY: /IM)
            for k in ("/BG", "/IM"):
                if k in xobj:
                    return str(xobj[k].stream_dict.get("/ColorSpace"))
            return "?"

    assert _bg_colorspace(out_default) == "/DeviceRGB"
    assert _bg_colorspace(out_mono) == "/DeviceGray"


def test_cli_doctor_reports_jpeg2000_and_jbig2(capsys) -> None:  # type: ignore[no-untyped-def]
    """--doctor surfaces JPEG2000 (Pillow/OpenJPEG) + jbig2enc availability so
    users can diagnose why text-only compression fell back to flate.
    """
    rc = main(["--doctor"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "JPEG2000" in out
    assert "jbig2enc" in out
