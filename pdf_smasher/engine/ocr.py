"""Thin Tesseract wrapper: image -> word-level bounding boxes.

Uses pytesseract's ``image_to_data`` in ``TSV`` output format. Returns a list
of :class:`WordBox` with integer pixel coords + per-word confidence.

Cross-host note: Tesseract's LSTM output is NOT bit-deterministic across
hosts (float32 BLAS ordering varies). Tests assert on content, not on exact
bounding-box pixel values. See ``docs/SPEC.md`` §12.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

import pytesseract
from PIL import Image

from pdf_smasher._pillow_hardening import ensure_capped
from pdf_smasher.exceptions import OcrTimeoutError

ensure_capped()

_BLOCK_OF_TEXT_PSM = "6"  # assume a single uniform block of text

# Default per-call timeout for Tesseract in seconds. Bounds a pathological
# Tesseract subprocess (wedged on a page that trips an LSTM edge case)
# from hanging the whole process indefinitely. Matches the upper end of
# CompressOptions.per_page_timeout_seconds (120s) — workers already cap
# at that number, so this is a belt-and-suspenders default for any
# direct caller that didn't thread the option through.
DEFAULT_OCR_TIMEOUT_SECONDS: float = 120.0


@dataclass(frozen=True)
class WordBox:
    """One word of OCR output with its bounding box and confidence."""

    text: str
    x: int
    y: int
    width: int
    height: int
    confidence: float  # 0-100 per Tesseract's scale; -1 for skipped rows in TSV


def tesseract_word_boxes(
    image: Image.Image,
    *,
    language: str = "eng",
    timeout_seconds: float = DEFAULT_OCR_TIMEOUT_SECONDS,
) -> list[WordBox]:
    """Run Tesseract on ``image`` and return one :class:`WordBox` per recognized word.

    Parameters
    ----------
    image:
        Input PIL image. RGB or grayscale both work.
    language:
        Tesseract language code (e.g. ``"eng"``, ``"eng+spa"``).
    timeout_seconds:
        Hard per-call timeout. Passed to ``pytesseract.image_to_data`` as
        the ``timeout=`` kwarg — pytesseract uses it to kill the
        Tesseract subprocess if it overruns. On timeout we re-raise as
        :class:`OcrTimeoutError` (subclass of :class:`CompressError`) so
        callers can distinguish a timeout from a crash. Default
        :data:`DEFAULT_OCR_TIMEOUT_SECONDS`.

    Returns
    -------
    list[WordBox]
        Empty list if nothing recognized.

    Raises
    ------
    OcrTimeoutError
        If Tesseract exceeded ``timeout_seconds``.
    """
    config = f"--psm {_BLOCK_OF_TEXT_PSM}"
    try:
        data = pytesseract.image_to_data(
            image,
            lang=language,
            config=config,
            output_type=pytesseract.Output.DICT,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        msg = (
            f"Tesseract OCR exceeded {timeout_seconds}s timeout on "
            f"{image.size[0]}x{image.size[1]} image"
        )
        raise OcrTimeoutError(msg) from exc
    except RuntimeError as exc:
        # pytesseract raises RuntimeError("Tesseract process timeout")
        # on some versions when it kills the subprocess. Translate that
        # too so callers get the same typed surface.
        if "timeout" in str(exc).lower():
            msg = (
                f"Tesseract OCR exceeded {timeout_seconds}s timeout on "
                f"{image.size[0]}x{image.size[1]} image"
            )
            raise OcrTimeoutError(msg) from exc
        raise

    boxes: list[WordBox] = []
    for i, text in enumerate(data["text"]):
        if not text or not text.strip():
            continue
        try:
            conf = float(data["conf"][i])
        except TypeError, ValueError:
            conf = -1.0
        # Clamp confidence to [0, 100]; Tesseract occasionally emits -1 for
        # "rejected" rows which we already filtered above on the text check.
        conf = max(0.0, min(100.0, conf))
        boxes.append(
            WordBox(
                text=text,
                x=int(data["left"][i]),
                y=int(data["top"][i]),
                width=int(data["width"][i]),
                height=int(data["height"][i]),
                confidence=conf,
            ),
        )
    return boxes
