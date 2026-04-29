"""Regression: OCR path must honor a timeout and not hang forever.

Reviewer: ``_ocr_pool`` ExitStack used an implicit ``wait=True`` on
ThreadPoolExecutor.__exit__; ``pytesseract.image_to_data`` had no
timeout. A wedged Tesseract subprocess could hang the whole process
indefinitely.

Fix: thread ``timeout_seconds`` through ``tesseract_word_boxes`` and
pass it as ``timeout=`` to ``image_to_data``. Pytesseract translates
the kwargs into ``subprocess.TimeoutExpired``; our wrapper re-raises
as a typed :class:`OcrTimeoutError` (subclass of CompressError) so
callers can distinguish "we killed tesseract" from "tesseract crashed."
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest
from PIL import Image

from hankpdf.engine.ocr import tesseract_word_boxes
from hankpdf.exceptions import OcrTimeoutError


def test_ocr_timeout_raises_typed_error() -> None:
    """If Tesseract hangs, the wrapper surfaces a typed timeout rather
    than blocking forever."""
    img = Image.new("RGB", (100, 100), color="white")

    def _hang(*_a: object, **_k: object) -> None:
        raise subprocess.TimeoutExpired(cmd="tesseract", timeout=0.01)

    import pytesseract

    with patch.object(pytesseract, "image_to_data", _hang), pytest.raises(OcrTimeoutError):
        tesseract_word_boxes(img, language="eng", timeout_seconds=0.01)


def test_ocr_timeout_seconds_is_forwarded_to_pytesseract() -> None:
    """Regression check: whatever we pass as ``timeout_seconds`` must
    reach ``pytesseract.image_to_data`` as the ``timeout=`` kwarg so the
    subprocess kill actually happens in the child, not just our wrapper.
    """
    img = Image.new("RGB", (100, 100), color="white")
    captured: dict[str, object] = {}

    def _capture(*args: object, **kwargs: object) -> dict[str, list[str]]:
        captured.update(kwargs)
        # Return an empty TSV dict so the outer call finishes normally.
        return {"text": [], "conf": [], "left": [], "top": [], "width": [], "height": []}

    import pytesseract

    with patch.object(pytesseract, "image_to_data", _capture):
        tesseract_word_boxes(img, language="eng", timeout_seconds=42)

    assert captured.get("timeout") == 42, (
        f"timeout kwarg not forwarded to pytesseract; captured={captured}"
    )


def test_ocr_default_timeout_is_a_number() -> None:
    """Calls with no explicit timeout must still bound Tesseract — the
    default is a concrete number (per-page budget), not None."""
    img = Image.new("RGB", (100, 100), color="white")
    captured: dict[str, object] = {}

    def _capture(*args: object, **kwargs: object) -> dict[str, list[str]]:
        captured.update(kwargs)
        return {"text": [], "conf": [], "left": [], "top": [], "width": [], "height": []}

    import pytesseract

    with patch.object(pytesseract, "image_to_data", _capture):
        tesseract_word_boxes(img, language="eng")

    timeout = captured.get("timeout")
    assert isinstance(timeout, (int, float)), (
        f"default timeout must be numeric (not None); got {timeout!r}"
    )
    assert timeout > 0, f"default timeout must be > 0; got {timeout}"
