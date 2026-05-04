"""Smoke test: build the wheel, install into a clean venv, exercise the
canonical ``hankpdf`` import.

Catches partial renames (Scenario 3 from the v0.2.0 rename pre-mortem):
``hankpdf --doctor`` reports ``0.0.0`` because ``_version.py`` still
hardcodes the old dist name. This test would have caught it.
"""

from __future__ import annotations

import importlib.metadata
import os
import shutil
import subprocess
import sys
import warnings
from pathlib import Path

import pytest


def _hankpdf_version_from_pyproject() -> str:
    """Parse the pyproject ``[project].version`` literal once."""
    repo_root = Path(__file__).resolve().parents[2]
    pyproject = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    for raw in pyproject.splitlines():
        line = raw.strip()
        if line.startswith("version = "):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    msg = "version not found in pyproject.toml"
    raise RuntimeError(msg)


@pytest.mark.integration
def test_canonical_hankpdf_import_exposes_public_api() -> None:
    """``from hankpdf import compress, CompressOptions`` works without
    a deprecation warning. This is the post-rename canonical path."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        from hankpdf import (
            CompressOptions,
            CompressReport,
            compress,
            compress_stream,
            triage,
        )

    # Exercise the API surface enough to confirm the imports actually work,
    # not just that the names exist.
    opts = CompressOptions()
    assert opts.engine == "mrc"
    assert callable(compress)
    assert callable(compress_stream)
    assert callable(triage)
    assert CompressReport.__module__.startswith("hankpdf")


@pytest.mark.integration
def test_version_resolves_from_installed_dist_metadata() -> None:
    """``hankpdf.__version__`` MUST match the installed dist's PKG-INFO,
    NOT the dev fallback. This is the test that would have caught the
    Scenario 3 pre-mortem (silent ``0.0.0`` reporting). The dist lookup
    string in ``_version.py`` must reference ``hankpdf``, not the legacy
    ``pdf-smasher`` name.
    """
    import hankpdf

    try:
        installed = importlib.metadata.version("hankpdf")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip(
            "hankpdf not installed in this environment "
            "(dev checkout without `uv sync` / `pip install -e .`)"
        )
    assert hankpdf.__version__ == installed, (
        f"hankpdf.__version__={hankpdf.__version__!r} but PKG-INFO says {installed!r}; "
        "_version.py is reading from the wrong dist name or the dev fallback "
        "is shadowing the real lookup"
    )
    pyproject_version = _hankpdf_version_from_pyproject()
    assert installed == pyproject_version, (
        f"installed hankpdf {installed!r} != pyproject [project].version "
        f"{pyproject_version!r}; the wheel was built from a stale source tree"
    )


@pytest.mark.integration
@pytest.mark.slow
def test_clean_venv_install_exposes_cli_and_api(tmp_path: Path) -> None:
    """End-to-end: build the wheel, install it into a fresh venv, then
    confirm:
    1. ``hankpdf --version`` exits 0 and prints the pyproject version
    2. ``import hankpdf; from hankpdf import compress`` works
    3. ``importlib.metadata.version('hankpdf') == hankpdf.__version__``
    4. The legacy ``pdf_smasher`` import package no longer resolves.

    This is the test the rename pre-mortem flagged as missing — without
    it, partial renames (e.g. ``_version.py`` not updated) ship silently.
    """
    repo_root = Path(__file__).resolve().parents[2]
    expected_version = _hankpdf_version_from_pyproject()

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv not on PATH; install per docs/INSTALL.md")

    # Build the wheel into tmp_path/dist/. Using uv build to match the
    # release.yml workflow exactly.
    dist = tmp_path / "dist"
    dist.mkdir()
    subprocess.run(
        [uv, "build", "--out-dir", str(dist)],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    wheels = list(dist.glob("hankpdf-*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel; got {wheels}"
    wheel = wheels[0]
    assert f"hankpdf-{expected_version}" in wheel.name, (
        f"wheel filename {wheel.name} does not encode pyproject version {expected_version}"
    )

    # Create a clean venv. Use sys.executable as the interpreter so we
    # match the test runner's Python.
    venv_dir = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        check=True,
        capture_output=True,
    )
    venv_python = venv_dir / ("Scripts" if os.name == "nt" else "bin") / "python"
    venv_hankpdf = venv_dir / ("Scripts" if os.name == "nt" else "bin") / "hankpdf"

    # Install the wheel into the venv.
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--quiet", str(wheel)],
        check=True,
        capture_output=True,
    )

    # 1. Console script works and reports the right version.
    result = subprocess.run(
        [str(venv_hankpdf), "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert expected_version in result.stdout, (
        f"`hankpdf --version` printed {result.stdout!r}; expected to contain "
        f"{expected_version!r}. Likely cause: `_version.py` is using the dev "
        f"fallback because the dist-name lookup string is wrong."
    )

    # 2. Canonical import works without firing any DeprecationWarning.
    api_check = subprocess.run(
        [
            str(venv_python),
            "-c",
            "import warnings; "
            "warnings.simplefilter('error', DeprecationWarning); "
            "from hankpdf import compress, CompressOptions; "
            "print('hankpdf:OK')",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "hankpdf:OK" in api_check.stdout

    # 3. The legacy `pdf_smasher` import package was removed in v0.3.0.
    # `python -c "import pdf_smasher"` must exit non-zero with
    # ModuleNotFoundError.
    legacy_check = subprocess.run(
        [str(venv_python), "-c", "import pdf_smasher"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert legacy_check.returncode != 0, (
        "pdf_smasher must NOT be importable in v0.3.0+; "
        f"stdout={legacy_check.stdout!r} stderr={legacy_check.stderr!r}"
    )
    assert "ModuleNotFoundError" in legacy_check.stderr, (
        f"expected ModuleNotFoundError; stderr={legacy_check.stderr!r}"
    )

    # 4. importlib.metadata + hankpdf.__version__ agree.
    metadata_check = subprocess.run(
        [
            str(venv_python),
            "-c",
            "import importlib.metadata, hankpdf; "
            "assert importlib.metadata.version('hankpdf') == hankpdf.__version__, "
            'f\'metadata={importlib.metadata.version("hankpdf")} '
            "vs __version__={hankpdf.__version__}'; "
            "print(hankpdf.__version__)",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert expected_version in metadata_check.stdout
