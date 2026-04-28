"""Regression: W-STALE-CHUNK-FILES survives --quiet (correctness > silence).

Reviewers A + B: stale chunks are a correctness hazard. Downstream batch
jobs glob `{base}_*.pdf` and may ingest stale files from a prior run if a
new run produced fewer chunks. Those jobs typically run `--quiet` in cron.

Silencing the stale-chunk warning under --quiet therefore actively harms
correctness. The other chunk summary lines and warnings stay suppressed,
but W-STALE-CHUNK-FILES is a correctness warning and must fire regardless.
"""

from __future__ import annotations

import pikepdf
import pytest

from pdf_smasher.cli.main import main


def _make_pdf(tmp_path, n_pages: int = 2):  # type: ignore[no-untyped-def]
    pdf = pikepdf.new()
    for _ in range(n_pages):
        page = pdf.add_blank_page(page_size=(612, 792))
        img = pdf.make_stream(
            b"\x00" * 2048,
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Image,
            Width=10,
            Height=10,
            BitsPerComponent=8,
            ColorSpace=pikepdf.Name.DeviceRGB,
            Filter=pikepdf.Name.FlateDecode,
        )
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=img))
        page.Contents = pdf.make_stream(b"q 612 0 0 792 0 0 cm /Im0 Do Q\n")
    p = tmp_path / "in.pdf"
    pdf.save(p)
    return p


@pytest.mark.integration
def test_stale_chunks_warning_not_silenced_by_quiet(
    tmp_path,  # type: ignore[no-untyped-def]
    capsys,  # type: ignore[no-untyped-def]
) -> None:
    """With --quiet and a pre-existing _099 chunk, W-STALE-CHUNK-FILES
    must still fire (stderr), while the other summary/warning lines stay
    suppressed."""
    in_path = _make_pdf(tmp_path, n_pages=2)
    out_path = tmp_path / "smol.pdf"
    # Seed a stale file with a high index.
    stale = tmp_path / "smol_099.pdf"
    stale.write_bytes(b"%PDF-1.7\n%stale\n")
    rc = main(
        [
            str(in_path),
            "-o",
            str(out_path),
            "--max-output-mb",
            "0.005",
            "--accept-drift",
            "--min-ratio",
            "0",
            "--quiet",
        ]
    )
    assert rc == 0, f"rc={rc}"
    err = capsys.readouterr().err
    assert "[W-STALE-CHUNK-FILES]" in err, (
        f"stale-chunk warning must survive --quiet (correctness invariant); stderr was:\n{err!r}"
    )
    # Sanity: other chunk summary chrome IS suppressed under --quiet. The
    # 'wrote N chunks' line must NOT appear.
    assert "wrote " not in err or "wrote these before" in err, (
        "chunk-count summary leaked past --quiet; got:\n{err!r}"
    )
