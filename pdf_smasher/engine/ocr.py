"""Thin Tesseract wrapper: image -> word-level bounding boxes.

Uses pytesseract's ``image_to_data`` in ``TSV`` output format. Returns a list
of :class:`WordBox` with integer pixel coords + per-word confidence.

Cross-host note: Tesseract's LSTM output is NOT bit-deterministic across
hosts (float32 BLAS ordering varies). Tests assert on content, not on exact
bounding-box pixel values. See ``docs/SPEC.md`` §12.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytesseract
from PIL import Image

_BLOCK_OF_TEXT_PSM = "6"  # assume a single uniform block of text


@dataclass(frozen=True)
class WordBox:
    """One word of OCR output with its bounding box and confidence."""

    text: str
    x: int
    y: int
    width: int
    height: int
    confidence: float  # 0-100 per Tesseract's scale; -1 for skipped rows in TSV


def tesseract_word_boxes(image: Image.Image, *, language: str = "eng") -> list[WordBox]:
    """Run Tesseract on ``image`` and return one :class:`WordBox` per recognized word.

    Parameters
    ----------
    image:
        Input PIL image. RGB or grayscale both work.
    language:
        Tesseract language code (e.g. ``"eng"``, ``"eng+spa"``).

    Returns
    -------
    list[WordBox]
        Empty list if nothing recognized.
    """
    config = f"--psm {_BLOCK_OF_TEXT_PSM}"
    data = pytesseract.image_to_data(
        image,
        lang=language,
        config=config,
        output_type=pytesseract.Output.DICT,
    )

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
