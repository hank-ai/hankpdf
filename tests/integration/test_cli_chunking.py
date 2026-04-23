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
        "--min-ratio", "0",  # blank-PDF test fixtures compress <1.5x
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
        "--min-ratio", "0",  # blank-PDF test fixtures compress <1.5x
    ])
    assert rc == 0
    err = capsys.readouterr().err
    assert "stale" in err.lower(), (
        f"expected 'stale' in stderr warning; got: {err!r}"
    )


@pytest.mark.integration
def test_max_output_mb_zero_rejected_at_parse_time(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    """--max-output-mb 0 is nonsense. argparse must reject at parse time
    via the _positive_float type validator (exits SystemExit(2)) rather
    than crashing at the end of a full pipeline run when
    split_pdf_by_size tries to build chunks for max_bytes=0.
    """
    in_path = _make_big_pdf(tmp_path, n_pages=1)
    out_path = tmp_path / "out.pdf"
    with pytest.raises(SystemExit) as exc_info:
        main([str(in_path), "-o", str(out_path), "--max-output-mb", "0"])
    assert exc_info.value.code == 2, (
        f"expected SystemExit(2) from argparse, got {exc_info.value.code}"
    )
    err = capsys.readouterr().err
    assert "--max-output-mb" in err


@pytest.mark.integration
def test_max_output_mb_negative_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    in_path = _make_big_pdf(tmp_path, n_pages=1)
    out_path = tmp_path / "out.pdf"
    with pytest.raises(SystemExit) as exc_info:
        main([str(in_path), "-o", str(out_path), "--max-output-mb", "-5"])
    assert exc_info.value.code == 2


@pytest.mark.integration
def test_chunk_pad_width_scales_beyond_999(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Regression: DCR Wave 2 flagged the hard-coded {:03d} pad width
    as sort-breaking past 999 chunks. Pad must scale to len(str(total)).

    We monkeypatch split_pdf_by_size to return 1200 tiny chunks so we
    don't have to actually build a 1200-page PDF.
    """
    import pdf_smasher.cli.main as cli_main

    in_path = _make_big_pdf(tmp_path, n_pages=2)
    out_path = tmp_path / "smol.pdf"

    fake_chunks = [b"%PDF-1.7\n%fake\n"] * 1200

    def fake_split(pdf_bytes, *, max_bytes):  # type: ignore[no-untyped-def]
        return fake_chunks

    monkeypatch.setattr(cli_main, "split_pdf_by_size", fake_split)
    # Avoid interfering with chunking.split_pdf_by_size's other callers;
    # cli_main holds the direct reference used in main().

    rc = main([
        str(in_path),
        "-o", str(out_path),
        "--max-output-mb", "0.1",
        "--accept-drift",
        "--min-ratio", "0",
    ])
    assert rc == 0
    # Expect 4-digit padded names (len(str(1200))==4): smol_0001.pdf ... smol_1200.pdf
    first = tmp_path / "smol_0001.pdf"
    last = tmp_path / "smol_1200.pdf"
    assert first.exists(), f"expected zero-padded 4-digit names; listing: {sorted(p.name for p in tmp_path.iterdir())[:5]}"
    assert last.exists()


@pytest.mark.integration
def test_single_chunk_oversize_warns(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    """When --max-output-mb is smaller than the compressed single page,
    the splitter returns 1 chunk but that chunk still exceeds the cap.
    The multi-chunk branch already warns about oversize chunks; the
    single-chunk branch silently honored neither the cap nor the
    warning. DCR Wave 2 flagged this — add a stderr warning."""
    in_path = _make_big_pdf(tmp_path, n_pages=1)
    out_path = tmp_path / "smol.pdf"
    # 0.001 MB = 1024 bytes. A compressed single page is ~15 KB, so the
    # single chunk will exceed the cap.
    rc = main([
        str(in_path),
        "-o", str(out_path),
        "--max-output-mb", "0.001",
        "--accept-drift",
        "--min-ratio", "0",
    ])
    assert rc == 0, f"expected EXIT_OK=0, got {rc}"
    err = capsys.readouterr().err
    assert "--max-output-mb" in err, (
        f"expected --max-output-mb in warning; got: {err!r}"
    )
    assert "exceed" in err.lower() or "over" in err.lower(), (
        f"expected 'exceed' or 'over' in warning; got: {err!r}"
    )


@pytest.mark.integration
def test_empty_pages_spec_on_pdf_returns_usage_exit(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    """Empty --pages in PDF output mode must return EXIT_USAGE=40,
    matching the image-export path. Previously this reached compress()
    which raised CompressError and routed to EXIT_ENGINE_ERROR=30."""
    in_path = _make_big_pdf(tmp_path, n_pages=2)
    out_path = tmp_path / "out.pdf"
    rc = main([str(in_path), "-o", str(out_path), "--pages", ""])
    assert rc == 40, f"expected EXIT_USAGE=40, got {rc}"
    err = capsys.readouterr().err
    assert "--pages" in err
