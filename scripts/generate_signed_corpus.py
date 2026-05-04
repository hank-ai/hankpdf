"""Generate signed-PDF fixtures for tests/corpus/signed/.

Test-only — uses pyhanko (the ``test`` extra dependency).

Outputs:
  simple_signed.pdf       — single approval signature
  certifying_signed.pdf   — DocMDP certifying signature (no changes allowed)
  multi_signed.pdf        — two approval signatures (incremental)

Run::

    uv run --extra test python scripts/generate_signed_corpus.py tests/corpus/signed/
"""

from __future__ import annotations

import argparse
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pikepdf
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import PdfSignatureMetadata, signers
from pyhanko.sign.fields import MDPPerm
from pyhanko.sign.signers import SimpleSigner


def _empty_pdf() -> bytes:
    """A minimal one-page PDF as the substrate for signing."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def _self_signed_signer(name: str) -> SimpleSigner:
    """Generate a self-signed cert in-memory and load it as a pyHanko SimpleSigner.

    pyHanko expects asn1crypto types, but we don't want to hand-roll those —
    so we make a PKCS#12 archive with the ``cryptography`` library and feed
    it back via ``SimpleSigner.load_pkcs12_data``, which converts internally.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, name)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    p12 = pkcs12.serialize_key_and_certificates(
        name=name.encode("utf-8"),
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.NoEncryption(),
    )
    signer: SimpleSigner = SimpleSigner.load_pkcs12_data(p12, other_certs=[])
    return signer


def make_simple_signed(out: Path) -> None:
    src = io.BytesIO(_empty_pdf())
    writer = IncrementalPdfFileWriter(src)
    signer = _self_signed_signer("hankpdf test signer")
    with out.open("wb") as fh:
        signers.PdfSigner(
            PdfSignatureMetadata(field_name="Sig1"), signer=signer
        ).sign_pdf(writer, output=fh)


def make_certifying_signed(out: Path) -> None:
    src = io.BytesIO(_empty_pdf())
    writer = IncrementalPdfFileWriter(src)
    signer = _self_signed_signer("hankpdf test certifier")
    with out.open("wb") as fh:
        signers.PdfSigner(
            PdfSignatureMetadata(
                field_name="Sig1",
                certify=True,
                docmdp_permissions=MDPPerm.NO_CHANGES,
            ),
            signer=signer,
        ).sign_pdf(writer, output=fh)


def make_multi_signed(out: Path) -> None:
    src = io.BytesIO(_empty_pdf())
    writer = IncrementalPdfFileWriter(src)
    signer_a = _self_signed_signer("hankpdf test signer A")
    intermediate = io.BytesIO()
    signers.PdfSigner(
        PdfSignatureMetadata(field_name="Sig1"), signer=signer_a
    ).sign_pdf(writer, output=intermediate)
    intermediate.seek(0)
    writer = IncrementalPdfFileWriter(intermediate)
    signer_b = _self_signed_signer("hankpdf test signer B")
    with out.open("wb") as fh:
        signers.PdfSigner(
            PdfSignatureMetadata(field_name="Sig2"), signer=signer_b
        ).sign_pdf(writer, output=fh)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("out_dir", type=Path)
    args = p.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    make_simple_signed(args.out_dir / "simple_signed.pdf")
    make_certifying_signed(args.out_dir / "certifying_signed.pdf")
    make_multi_signed(args.out_dir / "multi_signed.pdf")
    print(f"wrote 3 signed-PDF fixtures to {args.out_dir}")


if __name__ == "__main__":
    main()
