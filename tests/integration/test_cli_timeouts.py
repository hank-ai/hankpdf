"""Regression: CLI timeout flags must reject non-positive values at parse
time, not accept them and fail later inside the pipeline.

With ``per_page_timeout_seconds=0``, ``future.result(timeout=0)`` raises
TimeoutError instantly — every page fails with PerPageTimeoutError, deep
inside the engine. The user got EXIT_ENGINE_ERROR for a flag they got
wrong at the CLI. Argparse-time validation fails fast with EXIT_USAGE=2
(argparse's conventional exit code) and a clear ``invalid value for
--per-page-timeout-seconds`` message.

``--max-workers`` got the same treatment in commit ``9b92d60``; these
are its sibling flags.
"""

from __future__ import annotations

import pytest

from pdf_smasher.cli.main import main


@pytest.mark.parametrize(
    "flag", ["--per-page-timeout-seconds", "--total-timeout-seconds"],
)
@pytest.mark.parametrize("bad", ["0", "-1", "-100"])
def test_timeout_flag_rejects_non_positive(flag: str, bad: str) -> None:
    """Argparse must reject with SystemExit(2), not accept and fail later."""
    with pytest.raises(SystemExit) as ex:
        main(["/dev/null", "-o", "/tmp/nope.pdf", flag, bad])
    # argparse exits with 2 on usage errors
    assert ex.value.code == 2


@pytest.mark.parametrize(
    "flag", ["--per-page-timeout-seconds", "--total-timeout-seconds"],
)
def test_timeout_flag_rejects_non_integer(flag: str) -> None:
    """Argparse must reject non-integer values (e.g., floats) with SystemExit(2)."""
    with pytest.raises(SystemExit) as ex:
        main(["/dev/null", "-o", "/tmp/nope.pdf", flag, "not-a-number"])
    assert ex.value.code == 2


def test_timeout_flags_accept_positive_int() -> None:
    """Positive int must pass parse-time validation.

    We probe the argparse layer directly via ``_parser().parse_args``
    so the test stays hermetic (no filesystem, no pipeline) and any
    downstream failure can't mask a regression in the parser.
    """
    from pdf_smasher.cli.main import _parser

    ns = _parser().parse_args([
        "/dev/null",
        "-o", "/tmp/nope.pdf",
        "--per-page-timeout-seconds", "60",
        "--total-timeout-seconds", "600",
    ])
    assert ns.per_page_timeout_seconds == 60
    assert ns.total_timeout_seconds == 600
