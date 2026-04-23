"""CLI-level integration tests for --max-output-mb."""

from __future__ import annotations

import pikepdf
import pytest

from pdf_smasher.cli.main import main


def _make_big_pdf(tmp_path, n_pages: int = 4, payload_kb_per_page: int = 250):  # type: ignore[no-untyped-def]
    """Produce an N-page PDF whose post-compression serialized size is
    large enough that a small per-chunk cap forces multi-chunk output.

    The MRC pipeline rasterizes+re-encodes pages, so adding heavy
    filler streams doesn't help — post-compression output ends up
    ~15KB/page regardless. Instead, we rely on natural per-page size
    (~15KB) and pick a tiny --max-output-mb cap in the test.
    """
    pdf = pikepdf.new()
    for _ in range(n_pages):
        pdf.add_blank_page(page_size=(612, 792))
    path = tmp_path / "big.pdf"
    pdf.save(path)
    return path


@pytest.mark.integration
def test_chunked_output_uses_zero_padded_1_indexed_names(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """With --max-output-mb triggering chunking, output files must be
    named {base}_NNN{ext} (zero-padded, 1-indexed). DCR Wave 1 flagged
    the 0-indexed no-pad form as sort-breaking past 9 chunks.

    Uses a tiny 0.005 MB (~5 KB) cap to force chunking — each page
    serializes to several KB after compression, so we get one chunk
    per page.
    """
    in_path = _make_big_pdf(tmp_path, n_pages=4)
    out_path = tmp_path / "smol.pdf"
    rc = main([
        str(in_path),
        "-o", str(out_path),
        "--max-output-mb", "0.005",
        "--accept-drift",
    ])
    assert rc == 0, f"expected EXIT_OK=0, got {rc}"
    chunks = sorted(tmp_path.glob("smol_*.pdf"))
    assert len(chunks) >= 2, f"expected chunking; got {chunks}"
    for p in chunks:
        stem = p.stem
        suffix_chunk = stem.rsplit("_", 1)[-1]
        assert suffix_chunk.isdigit(), f"expected numeric suffix, got {stem}"
        assert len(suffix_chunk) == 3, f"expected 3-digit zero-pad, got {stem}"
    first_chunks = [p for p in chunks if p.stem.endswith("_001")]
    assert len(first_chunks) == 1, (
        f"expected one _001 chunk; got chunks={chunks}"
    )


@pytest.mark.integration
def test_chunked_output_warns_on_stale_siblings(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    """If a chunked write would leave stale _099 files from a prior run,
    warn the user. DCR Wave 1: silent stomp on pre-existing chunks is a
    correctness risk."""
    in_path = _make_big_pdf(tmp_path, n_pages=3)
    (tmp_path / "smol_099.pdf").write_bytes(b"stale data")
    out_path = tmp_path / "smol.pdf"
    rc = main([
        str(in_path),
        "-o", str(out_path),
        "--max-output-mb", "0.005",
        "--accept-drift",
    ])
    assert rc == 0
    err = capsys.readouterr().err
    assert "stale" in err.lower(), (
        f"expected 'stale' in stderr warning; got: {err!r}"
    )
