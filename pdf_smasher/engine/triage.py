"""Triage phase: cheap structural scan of an input PDF.

Never decodes image streams. Returns a :class:`TriageReport` with enough
information for the sanitize + recompress phases to decide how to handle
the input. See SPEC.md §4 for the weird-PDF taxonomy.
"""

from __future__ import annotations

import io
import re
from typing import Literal

import pikepdf

from pdf_smasher.exceptions import CorruptPDFError
from pdf_smasher.types import TriageReport

TriageClassification = Literal["proceed", "refuse", "pass-through", "require-password"]

_LINEARIZED_SCAN_WINDOW = 1024
_LINEARIZED_MARKER = re.compile(rb"/Linearized\s+[0-9.]+")


def _detect_linearized(pdf_bytes: bytes) -> bool:
    """Scan the first ~1 KB for the Linearized marker — cheaper than a full walk."""
    prefix = pdf_bytes[:_LINEARIZED_SCAN_WINDOW]
    return bool(_LINEARIZED_MARKER.search(prefix))


def _walk_dict_for_names(
    obj: object,
    target_names: frozenset[str],
    visited: set[int],
    depth: int = 0,
    max_depth: int = 12,
) -> set[str]:
    """Walk a pikepdf Object tree; return the subset of ``target_names`` found as keys.

    ``target_names`` is compared against dict keys as plain strings (leading
    slash stripped). Visited object IDs are tracked to avoid recursion on
    circular references.
    """
    hits: set[str] = set()
    if depth > max_depth:
        return hits
    if isinstance(obj, pikepdf.Dictionary):
        oid = id(obj)
        if oid in visited:
            return hits
        visited.add(oid)
        for key in obj.keys():  # pikepdf Dictionary iteration works at runtime
            bare = str(key).lstrip("/")
            if bare in target_names:
                hits.add(bare)
        for val in obj.values():  # type: ignore[operator]
            hits |= _walk_dict_for_names(val, target_names, visited, depth + 1, max_depth)
    elif isinstance(obj, pikepdf.Array):
        oid = id(obj)
        if oid in visited:
            return hits
        visited.add(oid)
        for item in obj:  # type: ignore[attr-defined]
            hits |= _walk_dict_for_names(item, target_names, visited, depth + 1, max_depth)
    return hits


def _detect_javascript(pdf: pikepdf.Pdf) -> bool:
    """True if any object tree contains JavaScript, JS, OpenAction with JS."""
    root = pdf.Root
    hits = _walk_dict_for_names(root, frozenset({"JavaScript", "JS"}), set())
    return bool(hits)


def _detect_embedded_files(pdf: pikepdf.Pdf) -> bool:
    root = pdf.Root
    hits = _walk_dict_for_names(root, frozenset({"EmbeddedFiles", "EmbeddedFile"}), set())
    return bool(hits)


def _detect_jbig2_streams(pdf: pikepdf.Pdf) -> bool:
    """Scan page resources for any XObject with /Filter /JBIG2Decode."""
    for page in pdf.pages:
        resources = page.obj.get("/Resources")
        if resources is None:
            continue
        xobjects = resources.get("/XObject")
        if xobjects is None:
            continue
        for xobj in xobjects.values():  # type: ignore[operator]
            filt = xobj.stream_dict.get("/Filter") if isinstance(xobj, pikepdf.Stream) else None
            if filt is None:
                continue
            filters = [filt] if not isinstance(filt, pikepdf.Array) else list(filt)  # type: ignore[call-overload]
            for f in filters:
                if str(f) == "/JBIG2Decode":
                    return True
    return False


def _detect_signature(pdf: pikepdf.Pdf) -> tuple[bool, bool]:
    """Return (is_signed, is_certifying_signature)."""
    acroform = pdf.Root.get("/AcroForm")
    if acroform is None:
        return False, False
    sig_flags = acroform.get("/SigFlags")
    is_signed = False
    if sig_flags is not None:
        try:
            is_signed = bool(int(sig_flags) & 1)  # bit 1: signatures exist
        except TypeError, ValueError:
            is_signed = False
    # Certifying-signature marker: /Perms /DocMDP
    perms = pdf.Root.get("/Perms")
    is_certifying = False
    if perms is not None and perms.get("/DocMDP") is not None:
        is_certifying = True
    # DocMDP implies a signature even if SigFlags wasn't set.
    return (is_signed or is_certifying), is_certifying


def _detect_pdf_a(pdf: pikepdf.Pdf) -> bool:
    """Look for pdfaid:part in the document XMP metadata."""
    metadata = pdf.Root.get("/Metadata")
    if metadata is None:
        return False
    try:
        raw = metadata.read_raw_bytes() if isinstance(metadata, pikepdf.Stream) else None
    except pikepdf.PdfError:
        return False
    if not raw:
        return False
    return b"pdfaid:part" in raw


def _detect_tagged(pdf: pikepdf.Pdf) -> bool:
    mark = pdf.Root.get("/MarkInfo")
    if mark is not None and bool(mark.get("/Marked")):
        return True
    return pdf.Root.get("/StructTreeRoot") is not None


def _get_producer(pdf: pikepdf.Pdf) -> str | None:
    info = pdf.trailer.get("/Info")
    if info is None:
        return None
    prod = info.get("/Producer")
    return str(prod) if prod is not None else None


def _classify(
    *,
    is_encrypted: bool,
    is_signed: bool,
    is_certified: bool,
) -> TriageClassification:
    if is_encrypted:
        return "require-password"
    if is_certified or is_signed:
        return "refuse"
    return "proceed"


def triage(pdf_bytes: bytes) -> TriageReport:
    """Classify a PDF input. See SPEC.md §4 for the full handling taxonomy."""
    is_linearized = _detect_linearized(pdf_bytes)

    # Probe encryption separately — pikepdf.open raises on missing password.
    is_encrypted = False
    try:
        pdf = pikepdf.open(io.BytesIO(pdf_bytes))
    except pikepdf.PasswordError:
        is_encrypted = True
        # We can still count pages but not inspect objects — return minimal
        # report with require-password classification.
        return TriageReport(
            pages=0,
            input_bytes=len(pdf_bytes),
            is_encrypted=True,
            is_signed=False,
            is_certified_signature=False,
            is_linearized=is_linearized,
            is_tagged=False,
            is_pdf_a=False,
            has_embedded_files=False,
            has_javascript=False,
            has_jbig2_streams=False,
            producer_fingerprint=None,
            classification="require-password",
        )
    except pikepdf.PdfError as e:
        msg = f"unable to parse PDF: {e}"
        raise CorruptPDFError(msg) from e

    try:
        pages = len(pdf.pages)
        is_signed, is_certified = _detect_signature(pdf)
        is_tagged = _detect_tagged(pdf)
        is_pdf_a = _detect_pdf_a(pdf)
        has_embedded_files = _detect_embedded_files(pdf)
        has_javascript = _detect_javascript(pdf)
        has_jbig2_streams = _detect_jbig2_streams(pdf)
        producer = _get_producer(pdf)
    finally:
        pdf.close()

    classification = _classify(
        is_encrypted=is_encrypted,
        is_signed=is_signed,
        is_certified=is_certified,
    )
    return TriageReport(
        pages=pages,
        input_bytes=len(pdf_bytes),
        is_encrypted=is_encrypted,
        is_signed=is_signed,
        is_certified_signature=is_certified,
        is_linearized=is_linearized,
        is_tagged=is_tagged,
        is_pdf_a=is_pdf_a,
        has_embedded_files=has_embedded_files,
        has_javascript=has_javascript,
        has_jbig2_streams=has_jbig2_streams,
        producer_fingerprint=producer,
        classification=classification,
    )
