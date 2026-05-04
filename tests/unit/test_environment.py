"""Unit tests for hankpdf._environment.

These tests stub subprocess.Popen and shutil.which to exercise the version
parsing and floor-comparison logic without depending on the host's
installed Tesseract/qpdf/jbig2enc.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hankpdf._environment import (
    QPDF_FLOOR,
    TESSERACT_FLOOR,
    EnvironmentReport,
    assert_environment_ready,
    get_environment_report,
    parse_qpdf_version,
    parse_tesseract_version,
)
from hankpdf.exceptions import EnvironmentError as HankEnvError


def _make_popen_factory(version_for):
    """Return a fake Popen constructor producing stubbed processes.

    ``version_for`` is a callable: cmd-list -> (stdout_str, returncode).
    The stdout string is encoded to UTF-8 bytes because the real
    ``_probe`` opens the subprocess in binary mode and decodes
    explicitly.
    """

    def _factory(cmd, **_kw):
        stdout, returncode = version_for(cmd)
        stdout_b = stdout.encode("utf-8") if isinstance(stdout, str) else stdout
        proc = MagicMock()
        proc.communicate.return_value = (stdout_b, b"")
        proc.returncode = returncode
        proc.kill = MagicMock()
        proc.wait = MagicMock()
        return proc

    return _factory


def test_parse_tesseract_version_handles_real_output() -> None:
    raw = "tesseract 5.3.4\n leptonica-1.84.1\n  libgif 5.2.1 ..."
    assert parse_tesseract_version(raw) == "5.3.4"


def test_parse_qpdf_version_handles_real_output() -> None:
    raw = "qpdf version 11.6.4\nRun qpdf --copyright to see ...\n"
    assert parse_qpdf_version(raw) == "11.6.4"


def test_parse_qpdf_version_returns_none_on_garbage() -> None:
    assert parse_qpdf_version("not a version string") is None


def test_assert_environment_ready_raises_when_tesseract_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_environment_report.cache_clear()
    monkeypatch.delenv("HANKPDF_SKIP_ENV_CHECK", raising=False)
    monkeypatch.setattr(
        "shutil.which",
        lambda tool: None if tool == "tesseract" else "/x",
    )

    def _versions(cmd):
        return f"{cmd[0]} version 99.0.0\n", 0

    monkeypatch.setattr("subprocess.Popen", _make_popen_factory(_versions))
    with pytest.raises(HankEnvError) as excinfo:
        assert_environment_ready()
    failures = excinfo.value.failures
    assert any(f.component == "tesseract" and f.reason == "missing" for f in failures)


def test_assert_environment_ready_raises_when_qpdf_below_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_environment_report.cache_clear()
    monkeypatch.delenv("HANKPDF_SKIP_ENV_CHECK", raising=False)
    monkeypatch.setattr("shutil.which", lambda _t: "/usr/bin/" + _t)

    def _versions(cmd):
        if cmd[0] == "qpdf":
            return "qpdf version 11.3.0\n", 0
        return f"{cmd[0]} version {TESSERACT_FLOOR}\n", 0

    monkeypatch.setattr("subprocess.Popen", _make_popen_factory(_versions))
    with pytest.raises(HankEnvError) as excinfo:
        assert_environment_ready()
    qpdf_failure = next(f for f in excinfo.value.failures if f.component == "qpdf")
    assert qpdf_failure.reason == "below-floor"
    assert qpdf_failure.found == "11.3.0"
    assert qpdf_failure.required == QPDF_FLOOR


def test_get_environment_report_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    get_environment_report.cache_clear()
    monkeypatch.delenv("HANKPDF_SKIP_ENV_CHECK", raising=False)
    calls = {"n": 0}

    def _versions(cmd):
        calls["n"] += 1
        return "qpdf version 11.6.4\n", 0

    monkeypatch.setattr("shutil.which", lambda _t: "/usr/bin/" + _t)
    monkeypatch.setattr("subprocess.Popen", _make_popen_factory(_versions))

    a = get_environment_report()
    b = get_environment_report()
    assert a is b
    # Exactly 3 subprocess.Popen calls expected: tesseract + qpdf + jbig2.
    # Pillow OpenJPEG probe uses an in-process encode test (no subprocess).
    assert calls["n"] == 3, (
        f"expected 3 subprocess calls (tesseract, qpdf, jbig2); got {calls['n']}"
    )


def test_skip_env_check_via_envvar(monkeypatch: pytest.MonkeyPatch) -> None:
    get_environment_report.cache_clear()
    monkeypatch.setenv("HANKPDF_SKIP_ENV_CHECK", "1")
    monkeypatch.setattr("shutil.which", lambda _t: None)
    report = assert_environment_ready()
    assert report.failures == ()


def test_environment_report_is_frozen() -> None:
    get_environment_report.cache_clear()
    report = EnvironmentReport(
        python_version="3.14.0",
        platform="linux",
        tesseract=None,
        qpdf=None,
        jbig2enc=None,
        openjpeg_via_pillow=None,
        pdfium_revision=None,
        pillow_max_image_pixels=0,
        failures=(),
    )
    with pytest.raises(AttributeError):
        report.python_version = "x"  # type: ignore[misc]
