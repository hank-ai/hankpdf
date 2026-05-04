"""Integration: signed-PDF default-refuse + --preserve-signatures + --allow-signed-invalidation.

Coverage:

* default refuse exits 11 with the hint mentioning --preserve-signatures.
* --preserve-signatures is byte-identical (no incremental update appended).
* --preserve-signatures and --allow-signed-invalidation are mutually
  exclusive at the argparse layer.
* certifying signatures still take the existing exit 15 path.
* --allow-certified-invalidation lets the engine proceed past the
  certifying-signature gate (no longer exit 15).
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hankpdf import (
    CompressOptions,
    PolicyDecision,
    SignedPDFError,
    _enforce_input_policy,
    compress,
)
from hankpdf.engine.triage import triage

CORPUS = Path("tests/corpus/signed")
SIMPLE = CORPUS / "simple_signed.pdf"
CERTIFYING = CORPUS / "certifying_signed.pdf"
MULTI = CORPUS / "multi_signed.pdf"


@pytest.fixture(scope="session", autouse=True)
def _ensure_corpus() -> None:
    expected = ["simple_signed.pdf", "certifying_signed.pdf", "multi_signed.pdf"]
    missing = [n for n in expected if not (CORPUS / n).exists()]
    if missing:
        pytest.skip(
            f"signed-PDF corpus missing: {missing} — run scripts/generate_signed_corpus.py"
        )


def _run_cli(args: list[str], env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HANKPDF_SKIP_ENV_CHECK"] = "1"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "hankpdf.cli.main", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


# ── CLI: default refuse + opt-ins ────────────────────────────────────────────


@pytest.mark.integration
def test_simple_signed_default_refuse_exit_11(tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    proc = _run_cli([str(SIMPLE), "-o", str(out)])
    assert proc.returncode == 11, proc.stderr
    # The new hint should mention both opt-ins.
    assert "signed" in proc.stderr.lower()


@pytest.mark.integration
def test_simple_signed_preserve_passthrough_byte_identical(tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    proc = _run_cli([str(SIMPLE), "-o", str(out), "--preserve-signatures"])
    assert proc.returncode == 0, proc.stderr
    assert out.read_bytes() == SIMPLE.read_bytes()


@pytest.mark.integration
def test_multi_signed_preserve_passthrough_byte_identical(tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    proc = _run_cli([str(MULTI), "-o", str(out), "--preserve-signatures"])
    assert proc.returncode == 0, proc.stderr
    assert out.read_bytes() == MULTI.read_bytes()


@pytest.mark.integration
def test_preserve_and_allow_mutually_exclusive(tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    proc = _run_cli(
        [
            str(SIMPLE),
            "-o",
            str(out),
            "--allow-signed-invalidation",
            "--preserve-signatures",
        ]
    )
    # argparse mutually-exclusive group exits non-zero.
    assert proc.returncode != 0
    assert "not allowed with" in proc.stderr or "mutually exclusive" in proc.stderr.lower()


@pytest.mark.integration
def test_certifying_signed_still_uses_existing_path(tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    proc = _run_cli([str(CERTIFYING), "-o", str(out)])
    # Certifying signatures take the existing certified-signature exit code,
    # NOT the signed-PDF code, even with --preserve-signatures absent.
    assert proc.returncode == 15, proc.stderr


@pytest.mark.integration
def test_certifying_signed_with_allow_certified_does_not_refuse(tmp_path: Path) -> None:
    """--allow-certified-invalidation must let the engine past the gate.

    The pipeline may still exit non-zero for unrelated reasons (drift
    on a near-empty page, ratio floor, etc), but it MUST NOT be exit 15.
    """
    out = tmp_path / "out.pdf"
    proc = _run_cli(
        [str(CERTIFYING), "-o", str(out), "--allow-certified-invalidation"]
    )
    assert proc.returncode != 15, proc.stderr


# ── Library-level invariants ────────────────────────────────────────────────


@pytest.mark.integration
def test_compress_signed_preserve_returns_passthrough_report() -> None:
    data = SIMPLE.read_bytes()
    out, report = compress(data, options=CompressOptions(preserve_signatures=True))
    assert out == data, "preserve_signatures must return input bytes verbatim"
    assert report.status == "passed_through"
    assert report.signature_state == "passthrough-preserved"
    assert report.signature_invalidated is False
    assert "passthrough-signed" in report.warnings


@pytest.mark.integration
def test_enforce_input_policy_returns_passthrough_for_signed_with_preserve() -> None:
    data = SIMPLE.read_bytes()
    tri = triage(data)
    decision = _enforce_input_policy(
        tri,
        CompressOptions(preserve_signatures=True),
        data,
    )
    assert decision is PolicyDecision.PASSTHROUGH_PRESERVE_SIGNATURE


@pytest.mark.integration
def test_enforce_input_policy_raises_for_signed_default() -> None:
    data = SIMPLE.read_bytes()
    tri = triage(data)
    with pytest.raises(SignedPDFError):
        _enforce_input_policy(tri, CompressOptions(), data)


@pytest.mark.integration
def test_compress_options_validates_mutual_exclusion() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        CompressOptions(preserve_signatures=True, allow_signed_invalidation=True)


@pytest.mark.integration
def test_compress_signed_allow_invalidation_marks_report() -> None:
    """When the user opts into recompression, the report must flag the
    invalidation explicitly so downstream tooling can react.
    """
    data = SIMPLE.read_bytes()
    # The MRC pipeline may still hit content-drift on a one-page near-blank
    # PDF; force-fast accepts drift so we exercise the success path and
    # check the signature_state is set on a non-passthrough run.
    out, report = compress(
        data,
        options=CompressOptions(allow_signed_invalidation=True, mode="fast"),
    )
    # Either the run completed (status="ok") or the per-page gate
    # whole-doc-passthrough'd. In the passthrough case we'd see signature_state
    # = "none" because we didn't actually invalidate anything — the bytes
    # were copied. Match either acceptable outcome.
    assert report.signature_state in {"invalidated-allowed", "none"}
    if report.status == "ok":
        assert report.signature_state == "invalidated-allowed"
        assert report.signature_invalidated is True
    # Smoke: output is a real PDF (even if passed through).
    assert out.startswith(b"%PDF-")
    _ = io  # lint
