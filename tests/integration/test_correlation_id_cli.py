"""CLI integration tests for --correlation-id."""

from __future__ import annotations

import os
import subprocess
import sys

from tests.conftest import minimal_pdf_bytes


def test_cli_correlation_id_flows_to_report(tmp_path):
    env = os.environ.copy()
    env["HANKPDF_SKIP_ENV_CHECK"] = "1"
    in_pdf = tmp_path / "in.pdf"
    in_pdf.write_bytes(minimal_pdf_bytes())
    out_pdf = tmp_path / "out.pdf"
    proc = subprocess.run(
        [sys.executable, "-m", "hankpdf.cli.main",
         str(in_pdf), "-o", str(out_pdf),
         "--correlation-id", "test-123"],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert "test-123" in proc.stderr or "test-123" in proc.stdout


def test_cli_correlation_id_rejects_bad_format(tmp_path):
    env = os.environ.copy()
    env["HANKPDF_SKIP_ENV_CHECK"] = "1"
    in_pdf = tmp_path / "in.pdf"
    in_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    proc = subprocess.run(
        [sys.executable, "-m", "hankpdf.cli.main",
         str(in_pdf), "-o", str(tmp_path / "out.pdf"),
         "--correlation-id", "bad id"],
        capture_output=True, text=True, env=env, timeout=10,
    )
    assert proc.returncode == 40, proc.stderr
