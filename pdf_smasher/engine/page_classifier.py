"""Per-page MRC-worthiness classifier.

Cheap signal: ``image_xobject_bytes / page_byte_budget`` per page. No
decoding, no rendering — just stream-length inspection. Returns one
bool per page (True = MRC-worthy, False = verbatim copy).

A page is MRC-worthy when its image-byte ratio meets or exceeds the
threshold. The default threshold (0.30) gives clean separation on
real-world inputs: native-export PDFs (PowerPoint/Word) sit at 0-15%;
scan-derived PDFs sit at 70-95%.

See ``docs/superpowers/specs/2026-04-27-per-page-selective-mrc-design.md``.
"""

from __future__ import annotations

import io

import pikepdf


def score_pages_for_mrc(
    pdf_bytes: bytes,
    *,
    password: str | None = None,
    min_image_byte_fraction: float = 0.30,
) -> list[bool]:
    """Return one bool per page: True = MRC-worthy, False = verbatim copy.

    Walks the input PDF once via pikepdf, computes per-page
    ``image_xobject_bytes / page_byte_budget``, returns True for pages
    whose ratio meets ``min_image_byte_fraction``. Pages where the
    analysis fails default to True (fail-safe — runs the existing MRC
    pipeline as a backstop).

    The denominator includes the page's content stream length plus the
    encoded byte size of every XObject (image AND form) referenced from
    that page's ``/Resources``. Note: nested Form XObjects are not
    recursively walked — image bytes inside a Form sub-resource are
    not counted, which biases the gate conservatively (toward more
    passthrough) on those rare layouts.
    """
    flags: list[bool] = []
    with pikepdf.open(io.BytesIO(pdf_bytes), password=password or "") as pdf:
        for page in pdf.pages:
            try:
                fraction = _page_image_byte_fraction(page)
            except Exception:  # noqa: BLE001 — defensive; any page-level error → MRC
                flags.append(True)
                continue
            flags.append(fraction >= min_image_byte_fraction)
    return flags


def _page_image_byte_fraction(page: pikepdf.Page) -> float:
    """Return ``image_xobject_bytes / page_byte_budget`` for one page.

    Numerator: sum of ``/Length`` for every ``/XObject /Image`` stream
    referenced from this page's ``/Resources/XObject`` dict.

    Denominator: ``len(content_stream) + sum(referenced_xobject_lengths)``,
    where referenced XObjects include both /Image and /Form (vector
    subforms). Floor at 1 byte to avoid division-by-zero on degenerate
    pages.
    """
    image_bytes = 0
    other_xobject_bytes = 0
    resources = page.obj.get("/Resources")
    if resources is not None:
        xobjects = resources.get("/XObject")
        if xobjects is not None:
            for xobj in xobjects.values():  # type: ignore[operator]
                if not isinstance(xobj, pikepdf.Stream):
                    continue
                length = int(xobj.get("/Length", 0) or 0)
                if xobj.get("/Subtype") == pikepdf.Name.Image:
                    image_bytes += length
                else:
                    other_xobject_bytes += length

    contents = page.obj.get("/Contents")
    content_bytes = 0
    if contents is not None:
        if isinstance(contents, pikepdf.Array):
            for s in contents:  # type: ignore[attr-defined]
                if isinstance(s, pikepdf.Stream):
                    content_bytes += int(s.get("/Length", 0) or 0)
        elif isinstance(contents, pikepdf.Stream):
            content_bytes = int(contents.get("/Length", 0) or 0)

    budget = max(1, content_bytes + image_bytes + other_xobject_bytes)
    return image_bytes / budget
