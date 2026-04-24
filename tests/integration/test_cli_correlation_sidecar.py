"""End-to-end tests for the correlation sidecar (Wave 5 / C3).

After every CLI run that writes output to a file, a
``{output_stem}_correlation.json`` sidecar should live next to the
output carrying the run's UUID4 correlation id and the input SHA-256.
Stderr lines also carry the short form of the same id.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pikepdf


def _make_small_pdf(path: Path) -> None:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.save(path)


def _run_cli(*argv: str, cwd: Path) -> tuple[int, str, str]:
    """Invoke hankpdf's main() in the current interpreter.

    Avoids subprocess round-trip so tests stay fast. Captures stdout +
    stderr via ``contextlib.redirect_*`` rather than pytest's capsys so
    we can drive it from anywhere in a test body.
    """
    import contextlib
    import io

    # Reset the audit singleton between tests so two runs don't share
    # correlation ids (the CLI re-binds on entry, but being explicit
    # makes the test intent clear).
    from pdf_smasher.audit import clear_correlation_id
    from pdf_smasher.cli.main import main

    clear_correlation_id()

    _cwd_prev = Path.cwd()
    try:
        import os

        os.chdir(cwd)
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            rc = main(list(argv))
        return rc, out_buf.getvalue(), err_buf.getvalue()
    finally:
        import os

        os.chdir(_cwd_prev)


def test_correlation_sidecar_is_written(tmp_path: Path) -> None:
    input_pdf = tmp_path / "in.pdf"
    output_pdf = tmp_path / "out.pdf"
    _make_small_pdf(input_pdf)

    rc, _stdout, _stderr = _run_cli(
        str(input_pdf),
        "-o",
        str(output_pdf),
        "--quiet",
        cwd=tmp_path,
    )
    # Passthrough or ok is fine — we only care that the sidecar exists.
    assert rc in {0, 2}, f"unexpected exit code {rc}; stderr={_stderr}"

    sidecar = tmp_path / "out_correlation.json"
    assert sidecar.is_file(), f"sidecar missing at {sidecar}"

    payload = json.loads(sidecar.read_text())
    # Top-level shape
    assert set(payload.keys()) >= {"run_id", "started_at", "build_info", "entries"}
    assert isinstance(payload["entries"], list)
    assert len(payload["entries"]) == 1
    entry = payload["entries"][0]
    # correlation_id is a UUID4 hex (32 chars)
    assert re.fullmatch(r"[a-f0-9]{32}", entry["correlation_id"])
    # input_sha256 has the "sha256:" prefix per the schema
    assert entry["input_sha256"].startswith("sha256:")
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", entry["input_sha256"])
    assert entry["input_size"] == input_pdf.stat().st_size
    assert entry["output_path"] == "out.pdf"


def test_correlation_id_in_stderr_matches_sidecar(tmp_path: Path) -> None:
    """Short-form corr= tag on stderr must line up with sidecar entry."""
    input_pdf = tmp_path / "in.pdf"
    output_pdf = tmp_path / "out.pdf"
    _make_small_pdf(input_pdf)

    # Drive without --quiet so the verifier-skipped warning emits and
    # carries the corr= tag.
    rc, _stdout, stderr = _run_cli(
        str(input_pdf),
        "-o",
        str(output_pdf),
        cwd=tmp_path,
    )
    assert rc in {0, 2}

    # Every warning line includes corr=<8-hex>
    corr_matches = re.findall(r"corr=([a-f0-9]{8})", stderr)
    assert corr_matches, f"no corr= tag found on stderr:\n{stderr}"
    short_id = corr_matches[0]

    sidecar = tmp_path / "out_correlation.json"
    payload = json.loads(sidecar.read_text())
    entry_cid = payload["entries"][0]["correlation_id"]
    assert entry_cid.startswith(short_id), (
        f"short stderr id {short_id!r} doesn't prefix sidecar id {entry_cid!r}"
    )


def test_correlation_sidecar_not_written_on_stdout(tmp_path: Path) -> None:
    """When output is -, we can't place a sidecar next to it. Skip."""
    input_pdf = tmp_path / "in.pdf"
    _make_small_pdf(input_pdf)

    # When redirecting stdout to a buffer, main() still writes to the
    # redirected file descriptor. We don't care about the output here,
    # only that no sidecar ends up in tmp_path for output name "-".
    before = set(tmp_path.glob("*_correlation.json"))
    import contextlib
    import io

    from pdf_smasher.audit import clear_correlation_id
    from pdf_smasher.cli.main import main

    clear_correlation_id()
    # main() calls sys.stdout.buffer.write, so redirect the raw byte
    # stream with a BytesIO attached.
    bio = io.BytesIO()

    class _FakeStdout:
        buffer = bio

    with contextlib.redirect_stdout(_FakeStdout()):  # type: ignore[arg-type]
        try:
            rc = main([str(input_pdf), "-o", "-", "--quiet"])
        except SystemExit as exc:
            rc = int(exc.code or 0)
    assert rc in {0, 2}

    after = set(tmp_path.glob("*_correlation.json"))
    assert before == after, "sidecar should not have been written for stdout output"
