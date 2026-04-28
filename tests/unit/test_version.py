"""Tests for hankpdf._version (Wave 5 / C1).

Confirms the version-resolution chain:

- ``__version__`` is set (either from PKG-INFO or the dev fallback).
- ``build_info()`` returns None outside a Docker image (no
  ``/etc/hankpdf/build-info.json``) and the parsed dict inside one.
- ``version_line()`` renders a recognizable single line in all paths.
"""

from __future__ import annotations

import json
from pathlib import Path

import hankpdf._version as vmod
from hankpdf._version import __version__, build_info, version_line


def test_version_string_is_nonempty() -> None:
    assert isinstance(__version__, str)
    assert __version__  # never empty — at minimum the dev fallback applies


def test_build_info_absent_outside_docker(monkeypatch) -> None:
    # Force the path check to miss even on the rare test host that
    # happens to have /etc/hankpdf/build-info.json present.
    monkeypatch.setattr(
        vmod,
        "_BUILD_INFO_PATH",
        Path("/nonexistent/hankpdf/build-info.json"),
    )
    build_info.cache_clear()
    try:
        assert build_info() is None
    finally:
        build_info.cache_clear()


def test_build_info_parses_present_file(tmp_path, monkeypatch) -> None:
    payload = {
        "version": "1.2.3",
        "git_sha": "abcdef1234567",
        "build_date": "2026-04-23T12:00:00Z",
        "base_image_digest": "sha256:deadbeef",
        # Synthetic 40-char hex — keep OFF the real pinned SHA so this fixture
        # can't shadow versions.json as a secondary source of truth.
        "jbig2enc_commit": "0" * 40,
        "qpdf_version": "12.2.0",
        "tesseract_version": "5.5.0",
        "leptonica_version": "1.84.1",
        "python_version": "3.14.4",
        "os_platform": "debian-trixie",
    }
    target = tmp_path / "build-info.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(vmod, "_BUILD_INFO_PATH", target)
    build_info.cache_clear()
    try:
        info = build_info()
        assert info is not None
        assert info["git_sha"] == "abcdef1234567"
        assert info["qpdf_version"] == "12.2.0"
    finally:
        build_info.cache_clear()


def test_build_info_rejects_non_dict(tmp_path, monkeypatch) -> None:
    # Someone hand-edits the file and writes a list. Don't crash; return
    # None so callers fall back to the bare version string.
    target = tmp_path / "build-info.json"
    target.write_text("[1, 2, 3]", encoding="utf-8")
    monkeypatch.setattr(vmod, "_BUILD_INFO_PATH", target)
    build_info.cache_clear()
    try:
        assert build_info() is None
    finally:
        build_info.cache_clear()


def test_build_info_rejects_malformed_json(tmp_path, monkeypatch) -> None:
    # Truncated write / corrupted file. Don't crash.
    target = tmp_path / "build-info.json"
    target.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(vmod, "_BUILD_INFO_PATH", target)
    build_info.cache_clear()
    try:
        assert build_info() is None
    finally:
        build_info.cache_clear()


def test_version_line_without_build_info(monkeypatch) -> None:
    monkeypatch.setattr(
        vmod,
        "_BUILD_INFO_PATH",
        Path("/nonexistent/hankpdf/build-info.json"),
    )
    build_info.cache_clear()
    try:
        line = version_line()
        assert line.startswith(f"hankpdf {__version__}")
        # python version is always appended even without build-info.json
        assert "python" in line
    finally:
        build_info.cache_clear()


def test_version_line_with_build_info(tmp_path, monkeypatch) -> None:
    payload = {
        "version": "1.2.3",
        "git_sha": "abcdef1234567",
        "build_date": "2026-04-23T12:00:00Z",
        "base_image_digest": "sha256:deadbeefcafebabefeeddeadbeefcafebabefeeddeadbeefcafebabefeeddeadbeef",
    }
    target = tmp_path / "build-info.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(vmod, "_BUILD_INFO_PATH", target)
    build_info.cache_clear()
    try:
        line = version_line()
        assert "git abcdef1" in line
        assert "built 2026-04-23T12:00:00Z" in line
        assert "image deadbee" in line  # first 7 hex chars after sha256:
        assert "python" in line
    finally:
        build_info.cache_clear()
