"""Tests for pdf_smasher.engine.triage — the structural scan phase.

Triage never decodes image streams. It answers: "what is this PDF, and
what's the handling policy?" (SPEC.md §4 weird-PDF taxonomy).
"""

from __future__ import annotations

import io

import pikepdf
import pytest

from pdf_smasher.engine.triage import triage


def _minimal_pdf() -> bytes:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def _multi_page_pdf(n: int) -> bytes:
    pdf = pikepdf.new()
    for _ in range(n):
        pdf.add_blank_page(page_size=(612, 792))
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


# ----- Baseline / page count -----


def test_triage_returns_triagereport_with_page_count() -> None:
    report = triage(_multi_page_pdf(3))
    assert report.pages == 3


def test_triage_records_input_bytes() -> None:
    data = _multi_page_pdf(1)
    report = triage(data)
    assert report.input_bytes == len(data)


def test_plain_pdf_classifies_as_proceed() -> None:
    report = triage(_minimal_pdf())
    assert report.classification == "proceed"
    assert not report.is_encrypted
    assert not report.is_signed
    assert not report.has_javascript


# ----- Encryption -----


def test_detects_user_password_encryption() -> None:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    buf = io.BytesIO()
    pdf.save(buf, encryption=pikepdf.Encryption(user="secret", owner="owner-secret"))
    report = triage(buf.getvalue())
    assert report.is_encrypted is True
    assert report.classification == "require-password"


# ----- Signatures -----


def test_detects_digital_signature_via_sigflags() -> None:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    # Minimally mark as signed: AcroForm SigFlags bit 1 = signatures exist.
    pdf.Root["/AcroForm"] = pikepdf.Dictionary(
        SigFlags=3,  # signatures exist + append-only
        Fields=pikepdf.Array([]),
    )
    buf = io.BytesIO()
    pdf.save(buf)
    report = triage(buf.getvalue())
    assert report.is_signed is True
    assert report.classification == "refuse"


def test_detects_certifying_signature() -> None:
    """/Perms /DocMDP indicates a certifying signature — stricter handling."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.Root["/AcroForm"] = pikepdf.Dictionary(SigFlags=3, Fields=pikepdf.Array([]))
    pdf.Root["/Perms"] = pikepdf.Dictionary(DocMDP=pikepdf.Dictionary(Type=pikepdf.Name.Sig))
    buf = io.BytesIO()
    pdf.save(buf)
    report = triage(buf.getvalue())
    assert report.is_signed is True
    assert report.is_certified_signature is True


# ----- JavaScript / active content -----


def test_detects_javascript_in_catalog() -> None:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.Root["/OpenAction"] = pikepdf.Dictionary(
        Type=pikepdf.Name.Action,
        S=pikepdf.Name.JavaScript,
        JS=pikepdf.String("app.alert('hi');"),
    )
    buf = io.BytesIO()
    pdf.save(buf)
    report = triage(buf.getvalue())
    assert report.has_javascript is True


# ----- Embedded files -----


def test_detects_embedded_files() -> None:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    attached = pdf.make_stream(b"secret-contents-here")
    attached.stream_dict = pikepdf.Dictionary(Type=pikepdf.Name.EmbeddedFile)
    pdf.Root["/Names"] = pikepdf.Dictionary(
        EmbeddedFiles=pikepdf.Dictionary(
            Names=pikepdf.Array(
                [
                    pikepdf.String("secret.txt"),
                    pikepdf.Dictionary(EF=pikepdf.Dictionary(F=attached)),
                ],
            ),
        ),
    )
    buf = io.BytesIO()
    pdf.save(buf)
    report = triage(buf.getvalue())
    assert report.has_embedded_files is True


# ----- JBIG2 streams (input already compressed with JBIG2) -----


def test_detects_jbig2_stream() -> None:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    # Synthesize an image XObject claiming JBIG2 encoding.
    xobj = pdf.make_stream(
        b"fake-jbig2-bytes",
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=1,
        Height=1,
        ColorSpace=pikepdf.Name.DeviceGray,
        BitsPerComponent=1,
        Filter=pikepdf.Name.JBIG2Decode,
    )
    pdf.pages[0].Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=xobj))
    buf = io.BytesIO()
    pdf.save(buf)
    report = triage(buf.getvalue())
    assert report.has_jbig2_streams is True


# ----- PDF/A detection -----


def test_detects_pdf_a_via_xmp() -> None:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    xmp = b"""<?xpacket begin='\xef\xbb\xbf' id='W5M0MpCehiHzreSzNTczkc9d'?>
<x:xmpmeta xmlns:x='adobe:ns:meta/' xmlns:pdfaid='http://www.aiim.org/pdfa/ns/id/'>
  <rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>
    <rdf:Description><pdfaid:part>2</pdfaid:part><pdfaid:conformance>U</pdfaid:conformance></rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
<?xpacket end='w'?>"""
    pdf.Root["/Metadata"] = pdf.make_stream(
        xmp,
        Type=pikepdf.Name.Metadata,
        Subtype=pikepdf.Name.XML,
    )
    buf = io.BytesIO()
    pdf.save(buf)
    report = triage(buf.getvalue())
    assert report.is_pdf_a is True


# ----- Linearization + tagging -----


def test_detects_linearized_pdf() -> None:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    buf = io.BytesIO()
    pdf.save(buf, linearize=True)
    report = triage(buf.getvalue())
    assert report.is_linearized is True


def test_detects_tagged_pdf() -> None:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.Root["/MarkInfo"] = pikepdf.Dictionary(Marked=True)
    pdf.Root["/StructTreeRoot"] = pikepdf.Dictionary(Type=pikepdf.Name.StructTreeRoot)
    buf = io.BytesIO()
    pdf.save(buf)
    report = triage(buf.getvalue())
    assert report.is_tagged is True


# ----- Graceful on corrupt input -----


def test_malformed_input_raises_corrupt_error() -> None:
    from pdf_smasher import CorruptPDFError

    with pytest.raises(CorruptPDFError):
        triage(b"not a pdf")
