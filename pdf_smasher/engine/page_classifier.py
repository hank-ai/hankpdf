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
    that page's ``/Resources``.

    **Conservative biases** (both push toward more pages routed to MRC,
    which is the safe direction — this is a pre-filter, not a verifier):

    1. Nested Form XObjects are not recursively walked. Image bytes
       inside a Form sub-resource are not counted toward the numerator.
       Layouts that hide images inside Form XObjects show as text-only.
    2. The classifier walks each page's *direct* ``/Resources/XObject``
       dict only. Resources inherited from the parent ``/Pages`` tree
       are not consulted. A page with parent-inherited image XObjects
       reads as having zero direct image bytes (fraction 0.0).

    Both biases are conservative: pages that actually have image content
    are over-classified as text-only and routed verbatim, which is
    indistinguishable from a no-image page from the user's perspective
    and saves wall-time. Pages classified as MRC-worthy on the direct
    ``/Resources`` walk go through the full pipeline anyway, where
    ``rasterize_page`` resolves inherited resources correctly.
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


def _stream_length(obj: pikepdf.Object) -> int:
    """Return the ``/Length`` of a pikepdf Stream, or 0 if missing/unparseable."""
    if not isinstance(obj, pikepdf.Stream):
        return 0
    raw = obj.get(pikepdf.Name.Length)
    if raw is None:
        return 0
    try:
        return int(raw)
    except TypeError, ValueError:
        return 0


def _xobject_byte_split(page: pikepdf.Page) -> tuple[int, int]:
    """Sum (image_xobject_bytes, other_xobject_bytes) for one page."""
    image_bytes = 0
    other_bytes = 0
    resources = page.obj.get(pikepdf.Name.Resources)
    if resources is None:
        return 0, 0
    xobjects = resources.get(pikepdf.Name.XObject)
    if xobjects is None:
        return 0, 0
    for xobj in xobjects.values():  # type: ignore[operator]
        length = _stream_length(xobj)
        if (
            isinstance(xobj, pikepdf.Stream)
            and xobj.get(pikepdf.Name.Subtype) == pikepdf.Name.Image
        ):
            image_bytes += length
        else:
            other_bytes += length
    return image_bytes, other_bytes


def _content_stream_bytes(page: pikepdf.Page) -> int:
    """Sum the encoded length of /Contents (single stream or array of streams)."""
    contents = page.obj.get(pikepdf.Name.Contents)
    if contents is None:
        return 0
    if isinstance(contents, pikepdf.Array):
        total = 0
        for s in contents:  # type: ignore[attr-defined]
            total += _stream_length(s)
        return total
    return _stream_length(contents)


def _page_image_byte_fraction(page: pikepdf.Page) -> float:
    """Return ``image_xobject_bytes / page_byte_budget`` for one page.

    Numerator: sum of ``/Length`` for every ``/XObject /Image`` stream
    referenced from this page's ``/Resources/XObject`` dict.

    Denominator: ``len(content_stream) + sum(referenced_xobject_lengths)``,
    where referenced XObjects include both /Image and /Form (vector
    subforms). Floor at 1 byte to avoid division-by-zero on degenerate
    pages.
    """
    image_bytes, other_xobject_bytes = _xobject_byte_split(page)
    content_bytes = _content_stream_bytes(page)
    budget = max(1, content_bytes + image_bytes + other_xobject_bytes)
    return image_bytes / budget
