"""CLI entry point smoke tests."""

from __future__ import annotations

from hankpdf import __version__
from hankpdf.cli.main import main


def test_cli_version_flag(capsys) -> None:  # type: ignore[no-untyped-def]
    rc = main(["--version"])
    assert rc == 0
    out = capsys.readouterr().out
    assert __version__ in out


def test_cli_no_args_is_usage_error(capsys) -> None:  # type: ignore[no-untyped-def]
    """Invoking without INPUT + -o must return exit 40 (usage)."""
    rc = main([])
    assert rc == 40
    err = capsys.readouterr().err
    assert "required" in err.lower()


def test_cli_doctor_exits_0_and_prints_env(capsys) -> None:  # type: ignore[no-untyped-def]
    rc = main(["--doctor"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "hankpdf" in out
    assert "python" in out.lower()
