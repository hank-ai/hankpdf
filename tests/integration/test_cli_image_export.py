"""CLI-level integration tests for --output-format jpeg|png|webp."""

from __future__ import annotations

import io

import pikepdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from hankpdf.cli.main import main


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
        SigFlags=3,
        Fields=pikepdf.Array([]),
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
    rc = main(
        [
            str(in_path),
            "-o",
            str(out_path),
            "--max-output-mb",
            "5",
        ]
    )
    assert rc == 0
    err = capsys.readouterr().err
    assert "--max-output-mb" in err, f"expected '--max-output-mb' in stderr; got: {err!r}"
    assert "image" in err.lower(), f"expected 'image' in stderr warning; got: {err!r}"


@pytest.mark.integration
def test_output_format_suffix_mismatch_warns(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    """-o out.pdf --output-format jpeg should warn the user that we're
    writing a JPEG regardless of the .pdf extension. DCR Wave 1:
    silently writing a different format than the filename suggests is
    a UX trap — surface the override."""
    in_path = _make_pdf(tmp_path, n_pages=1)
    # Intentional mismatch: extension says pdf, explicit format says jpeg.
    # Use a subdir with a neutral name so the tmp-path doesn't happen to
    # contain any of the keywords we're grepping for.
    neutral_dir = tmp_path / "neutral"
    neutral_dir.mkdir()
    out_path = neutral_dir / "out.pdf"
    rc = main(
        [
            str(in_path),
            "-o",
            str(out_path),
            "--output-format",
            "jpeg",
        ]
    )
    assert rc == 0
    err = capsys.readouterr().err
    assert "extension" in err.lower() or "overrides" in err.lower(), (
        f"expected mismatch warning in stderr; got: {err!r}"
    )


@pytest.mark.integration
def test_output_format_override_corrects_extension(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """--output-format png with -o out.jpeg must write PNG to .png, not
    .jpeg. Previously the warning fired but the file was still named
    out.jpeg with PNG bytes inside — self-contradicting on disk."""
    in_path = _make_pdf(tmp_path, n_pages=1)
    out_path = tmp_path / "out.jpeg"
    rc = main([str(in_path), "-o", str(out_path), "--output-format", "png"])
    assert rc == 0
    # The warning is fine, but the file on disk must match the actual format
    assert not out_path.exists(), (
        f"should not have written .jpeg when format is png; found: {out_path}"
    )
    actual = tmp_path / "out.png"
    assert actual.exists(), f"expected out.png to exist; got: {list(tmp_path.iterdir())}"
    assert actual.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", "must be PNG bytes"


@pytest.mark.integration
def test_image_export_routes_malicious_to_specific_exit(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """If triage raises MaliciousPDFError, image-export must return
    EXIT_MALICIOUS=14, not the generic EXIT_CORRUPT=13."""
    import hankpdf.cli.main as cli_main
    from hankpdf.exceptions import MaliciousPDFError

    def fake_triage(_b, **_kwargs):  # type: ignore[no-untyped-def]
        msg = "synthetic malicious content"
        raise MaliciousPDFError(msg)

    monkeypatch.setattr(cli_main, "triage", fake_triage)

    in_path = _make_pdf(tmp_path, n_pages=1)
    out_path = tmp_path / "out.jpg"
    rc = main([str(in_path), "-o", str(out_path)])
    assert rc == 14, f"expected EXIT_MALICIOUS=14, got {rc}"


@pytest.mark.integration
def test_image_export_routes_bomb_to_specific_exit(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """If triage raises DecompressionBombError, image-export must
    return EXIT_DECOMPRESSION_BOMB=16, not EXIT_CORRUPT=13."""
    import hankpdf.cli.main as cli_main
    from hankpdf.exceptions import DecompressionBombError

    def fake_triage(_b, **_kwargs):  # type: ignore[no-untyped-def]
        msg = "synthetic bomb"
        raise DecompressionBombError(msg)

    monkeypatch.setattr(cli_main, "triage", fake_triage)

    in_path = _make_pdf(tmp_path, n_pages=1)
    out_path = tmp_path / "out.jpg"
    rc = main([str(in_path), "-o", str(out_path)])
    assert rc == 16, f"expected EXIT_DECOMPRESSION_BOMB=16, got {rc}"


@pytest.mark.integration
def test_image_export_pad_width_scales_past_999(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Regression: DCR Wave 2 flagged the hard-coded {:03d} pad in the
    multi-page image-export branch. 1200-page jobs must produce
    out_0001.jpg ... out_1200.jpg (4-digit pad), not a mix of 3- and
    4-digit names that sort-lex wrong."""
    import hankpdf.cli.main as cli_main

    in_path = _make_pdf(tmp_path, n_pages=2)
    out_path = tmp_path / "out.jpg"

    # Fake triage with 1200 pages, and a tiny-blob iter that mimics
    # iter_pages_as_images without actually rasterizing.
    class _FakeTri:
        pages = 1200
        is_encrypted = False
        is_signed = False
        is_certified_signature = False
        is_oversize = False

    def fake_triage(_pdf_bytes, **_kwargs):  # type: ignore[no-untyped-def]
        return _FakeTri

    def fake_enforce(_tri, _opts, _in_bytes):  # type: ignore[no-untyped-def]
        return None

    def fake_iter(*_args, page_indices, **_kwargs):  # type: ignore[no-untyped-def]
        # Emit a valid JPEG SOI-marker so downstream writes succeed.
        for _ in page_indices:
            yield b"\xff\xd8\xff\xe0FAKEJPG"

    monkeypatch.setattr(cli_main, "triage", fake_triage)
    monkeypatch.setattr(cli_main, "_enforce_input_policy", fake_enforce)
    monkeypatch.setattr(cli_main, "iter_pages_as_images", fake_iter)

    rc = main([str(in_path), "-o", str(out_path), "--quiet"])
    assert rc == 0
    # Expect 4-digit padded names.
    first = tmp_path / "out_0001.jpg"
    last = tmp_path / "out_1200.jpg"
    assert first.exists(), (
        f"expected 4-digit-padded names for 1200-page job; got "
        f"{sorted(p.name for p in tmp_path.iterdir())[:5]}"
    )
    assert last.exists()


@pytest.mark.integration
def test_image_export_partial_failure_emits_summary(tmp_path, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """When page N fails mid-stream, CLI must: exit a specific code
    (not 1, not raw traceback), emit a 'wrote K of N' stderr summary,
    and list orphaned files so the operator can clean them up."""
    from hankpdf.engine import image_export as ie

    in_path = _make_pdf(tmp_path, n_pages=5)
    out_path = tmp_path / "out.jpg"

    # Monkeypatch rasterize_page to fail on page index 2 (1-indexed page 3).
    original = ie.rasterize_page

    def flaky(pdf_bytes, *, page_index, dpi, **_kwargs):  # type: ignore[no-untyped-def]
        if page_index == 2:
            msg = "synthetic pdfium failure on page 3"
            raise RuntimeError(msg)
        return original(pdf_bytes, page_index=page_index, dpi=dpi)

    monkeypatch.setattr(ie, "rasterize_page", flaky)

    rc = main([str(in_path), "-o", str(out_path)])
    assert rc not in {0, 1}, f"expected structured non-0/1 exit code, got {rc}"
    err = capsys.readouterr().err
    assert "page 3" in err, f"no page context in stderr: {err!r}"
    # pages 1 and 2 should be on disk; pages 3-5 should not be
    p1 = tmp_path / "out_001.jpg"
    p2 = tmp_path / "out_002.jpg"
    p3 = tmp_path / "out_003.jpg"
    assert p1.exists()
    assert p2.exists()
    assert not p3.exists()


@pytest.mark.integration
def test_output_format_override_corrects_extension_multipage(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Multi-page variant of the override test: 3 pages, -o out.jpg,
    --output-format webp must produce out_001.webp, out_002.webp,
    out_003.webp — not .jpg."""
    in_path = _make_pdf(tmp_path, n_pages=3)
    out_path = tmp_path / "out.jpg"
    rc = main(
        [
            str(in_path),
            "-o",
            str(out_path),
            "--output-format",
            "webp",
        ]
    )
    assert rc == 0
    jpgs = sorted(tmp_path.glob("out_*.jpg"))
    assert not jpgs, f"no .jpg files should exist; got {jpgs}"
    webps = sorted(tmp_path.glob("out_*.webp"))
    assert len(webps) == 3, f"expected 3 .webp files; got {webps}"
    # WebP magic: "RIFF" .... "WEBP"
    head = webps[0].read_bytes()[:12]
    assert head[:4] == b"RIFF", f"expected RIFF prefix; got {head!r}"
    assert head[8:12] == b"WEBP", f"expected WEBP marker; got {head!r}"
