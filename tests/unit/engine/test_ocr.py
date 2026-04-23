"""Tests for pdf_smasher.engine.ocr — thin Tesseract wrapper."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from pdf_smasher.engine.ocr import WordBox, tesseract_word_boxes


def _make_text_image(
    text: str,
    *,
    width: int = 800,
    height: int = 200,
    font_size: int = 48,
) -> Image.Image:
    """Render ``text`` onto a white canvas in black, centered-ish."""
    img = Image.new("RGB", (width, height), color="white")
    draw = ImageDraw.Draw(img)
    # Use a default bitmap font — deterministic across hosts, doesn't require a
    # specific TrueType to be installed.
    try:
        font = ImageFont.truetype("Arial.ttf", font_size)
    except OSError:
        font = ImageFont.load_default(size=font_size)
    draw.text((40, 60), text, fill="black", font=font)
    return img


def test_returns_list_of_wordbox() -> None:
    img = _make_text_image("HELLO")
    result = tesseract_word_boxes(img)
    assert isinstance(result, list)
    assert all(isinstance(b, WordBox) for b in result)


def test_detects_single_word() -> None:
    img = _make_text_image("HELLO")
    result = tesseract_word_boxes(img)
    texts = [b.text for b in result]
    assert "HELLO" in texts


def test_detects_multiple_words() -> None:
    img = _make_text_image("HELLO WORLD")
    result = tesseract_word_boxes(img)
    texts = {b.text for b in result}
    assert "HELLO" in texts
    assert "WORLD" in texts


def test_wordbox_has_bbox_within_image() -> None:
    img = _make_text_image("HELLO")
    result = tesseract_word_boxes(img)
    hello = next(b for b in result if b.text == "HELLO")
    assert 0 <= hello.x < img.width
    assert 0 <= hello.y < img.height
    assert hello.x + hello.width <= img.width
    assert hello.y + hello.height <= img.height
    assert hello.width > 0
    assert hello.height > 0


def test_wordbox_has_confidence_0_to_100() -> None:
    img = _make_text_image("HELLO")
    result = tesseract_word_boxes(img)
    for box in result:
        assert 0 <= box.confidence <= 100


def test_blank_image_returns_empty_list() -> None:
    img = Image.new("RGB", (400, 200), color="white")
    result = tesseract_word_boxes(img)
    assert result == []


def test_language_argument_accepted() -> None:
    img = _make_text_image("HELLO")
    # Should not raise — tesseract eng is installed per docs/INSTALL.md.
    tesseract_word_boxes(img, language="eng")
