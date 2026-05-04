"""Integration test: missing Tesseract surfaces as exit 17 with a friendly hint."""

from __future__ import annotations

import os
import subprocess
import sys


def test_cli_exits_17_when_tesseract_missing(tmp_path) -> None:
    # Hide tesseract by emptying PATH inside the subprocess invocation.
    env = os.environ.copy()
    env["PATH"] = ""  # nothing resolvable
    env["HANKPDF_SKIP_ENV_CHECK"] = "0"
    in_pdf = tmp_path / "input.pdf"
    in_pdf.write_bytes(b"%PDF-1.7\n%%EOF\n")  # malformed but argparse OK
    out_pdf = tmp_path / "out.pdf"
    proc = subprocess.run(
        [sys.executable, "-m", "hankpdf.cli.main", str(in_pdf), "-o", str(out_pdf)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 17, proc.stderr
    assert "tesseract" in proc.stderr.lower()
    assert "install" in proc.stderr.lower()


def test_doctor_renders_environment_report() -> None:
    """`hankpdf --doctor` must use the same EnvironmentReport infra as the
    boot check (no second source of truth).
    """
    from hankpdf.cli.main import _doctor_report

    out = _doctor_report()
    for token in ("tesseract", "qpdf", "jbig2"):
        assert token in out.lower()
