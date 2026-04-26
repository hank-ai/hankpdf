"""Canonical input hashing (SPEC.md §5).

Two PDFs that differ only in ``/ID``, ``/CreationDate``, ``/ModDate``, or
XMP timestamps canonicalize to the same hash. Two PDFs with genuinely
different content hash differently.

Callers can use this value for dedup, caching, audit, or sidecar
correlation. HankPDF itself stores nothing.
"""

from __future__ import annotations

import hashlib
import io

import pikepdf


def canonical_input_sha256(pdf_bytes: bytes, *, password: str | None = None) -> str:
    """Return the SHA-256 hex digest of a canonicalized form of ``pdf_bytes``."""
    with pikepdf.open(io.BytesIO(pdf_bytes), password=password or "") as pdf:
        # Strip metadata that changes per save / per producer but not content.
        if "/Info" in pdf.trailer:
            info = pdf.trailer["/Info"]
            for key in ("/CreationDate", "/ModDate", "/Producer", "/Creator"):
                if key in info:
                    del info[key]
        if "/ID" in pdf.trailer:
            del pdf.trailer["/ID"]
        # Drop XMP metadata entirely — timestamps and producer strings live there.
        if "/Metadata" in pdf.Root:
            del pdf.Root["/Metadata"]

        buf = io.BytesIO()
        pdf.save(buf, linearize=False, deterministic_id=True)
        return hashlib.sha256(buf.getvalue()).hexdigest()
