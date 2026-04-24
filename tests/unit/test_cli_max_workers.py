"""Regression: --max-workers is validated at argparse time.

Reviewer B: negative / absurd values used to be silently accepted (coerced
by downstream code). No feedback, no exit code. Fail fast with usage error.
"""

from __future__ import annotations

import pytest

from pdf_smasher.cli.main import main


@pytest.mark.parametrize("bad", ["-1", "-100", "257", "99999"])
def test_negative_or_huge_max_workers_is_usage_error(
    bad: str,
    tmp_path,  # type: ignore[no-untyped-def]
    capsys,  # type: ignore[no-untyped-def]
) -> None:
    """Negative or >256 values must exit 2 (argparse usage error).

    argparse exits 2 via SystemExit on ArgumentTypeError — that's the
    Python argparse contract. Our spec maps EXIT_USAGE=40 only for paths
    that reach main() past argparse; arg-validation errors are 2.
    """
    in_path = tmp_path / "in.pdf"
    in_path.write_bytes(b"%PDF-1.7\n")
    out_path = tmp_path / "out.pdf"
    with pytest.raises(SystemExit) as excinfo:
        main([str(in_path), "-o", str(out_path), "--max-workers", bad])
    # argparse.ArgumentTypeError → sys.exit(2)
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "max-workers" in err.lower() or "max_workers" in err.lower()


def test_zero_max_workers_accepted_as_auto(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """0 is the documented 'auto' sentinel — must parse cleanly."""
    import pikepdf

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    in_path = tmp_path / "in.pdf"
    pdf.save(in_path)
    out_path = tmp_path / "out.pdf"
    rc = main([str(in_path), "-o", str(out_path), "--max-workers", "0", "--quiet"])
    assert rc == 0, f"rc={rc}"


def test_one_max_workers_accepted_as_serial(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """1 is the documented 'serial' sentinel — must parse cleanly."""
    import pikepdf

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    in_path = tmp_path / "in.pdf"
    pdf.save(in_path)
    out_path = tmp_path / "out.pdf"
    rc = main([str(in_path), "-o", str(out_path), "--max-workers", "1", "--quiet"])
    assert rc == 0, f"rc={rc}"
