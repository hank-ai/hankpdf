"""CLI-level tests for the verifier-status stderr banner.

Default behavior is skip_verify=True so runs are fast, but silently
skipping the verifier is a UX trap — users read clean text output and
assume content was checked. The CLI must surface
verifier.status ("skipped" or "fail") on stderr when it's not "pass".
"""

from __future__ import annotations

import pikepdf
import pytest

from pdf_smasher.cli.main import main


def _make_blank_pdf(path) -> None:  # type: ignore[no-untyped-def]
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.save(path)


@pytest.mark.integration
def test_cli_banner_on_verifier_skipped(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    """Default skip_verify=True must print a clear stderr banner so users
    don't assume the output was content-verified.
    """
    in_path = tmp_path / "in.pdf"
    _make_blank_pdf(in_path)
    out_path = tmp_path / "out.pdf"

    rc = main([str(in_path), "-o", str(out_path)])  # default skip_verify=True
    assert rc == 0
    err = capsys.readouterr().err
    assert "verifier" in err.lower()
    assert "skip" in err.lower() or "not verified" in err.lower(), (
        f"expected 'skip' or 'not verified' in stderr; got: {err!r}"
    )


@pytest.mark.integration
def test_cli_no_banner_when_verifier_passed(tmp_path, capsys) -> None:
    """When --verify passes cleanly, no SKIP banner should appear — the
    existing text report is the positive signal."""
    in_path = tmp_path / "in.pdf"
    _make_blank_pdf(in_path)
    out_path = tmp_path / "out.pdf"

    rc = main([str(in_path), "-o", str(out_path), "--verify"])
    assert rc == 0
    err = capsys.readouterr().err
    # No SKIP banner for a passing verifier.
    assert "verifier was SKIPPED" not in err
    assert "not content-checked" not in err


@pytest.mark.integration
def test_cli_quiet_suppresses_verifier_banner(tmp_path, capsys) -> None:
    """--quiet must suppress the skip banner along with the other
    progress output — banners on stderr are still UX chrome."""
    in_path = tmp_path / "in.pdf"
    _make_blank_pdf(in_path)
    out_path = tmp_path / "out.pdf"

    rc = main([str(in_path), "-o", str(out_path), "--quiet"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "verifier was SKIPPED" not in err
