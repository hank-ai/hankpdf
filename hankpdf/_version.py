"""Version string. Advanced independently from engine_version.

Resolution order at runtime (module load):

1. ``importlib.metadata.version("hankpdf")`` — the installed dist's
   own PKG-INFO. This is the truth when HankPDF is pip-installed,
   ``uv sync``'d, or pulled from a wheel.
2. Fall back to the literal below for dev checkouts where no PKG-INFO
   has been written (e.g., running ``uv run python -m hankpdf`` on
   a fresh clone before any install).

``__engine_version__`` is a separate knob because the engine's behavior
contract (verifier thresholds, passthrough floors, etc.) advances on a
different cadence than the CLI's version string. Readers consuming
:class:`CompressReport` should pin on engine_version, not version.

If /etc/hankpdf/build-info.json exists (the Docker image writes it in
Wave 5 B3), :func:`build_info` returns the full build manifest so
``hankpdf --version`` and ``--doctor`` can surface git_sha + build_date.
"""

from __future__ import annotations

import json
import platform
from functools import lru_cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version
from pathlib import Path
from typing import Any

# Dev fallback. PKG-INFO wins at runtime when the package is installed.
_DEV_VERSION: str = "0.3.0"
_BUILD_INFO_PATH = Path("/etc/hankpdf/build-info.json")


def _resolve_version() -> str:
    """Return the installed dist version, or the dev fallback."""
    try:
        return _dist_version("hankpdf")
    except PackageNotFoundError:
        return _DEV_VERSION


__version__: str = _resolve_version()
__engine_version__: str = "0.0.0"


@lru_cache(maxsize=1)
def build_info() -> dict[str, Any] | None:
    """Return the image's build manifest, or None outside a Docker image.

    Written by docker/Dockerfile at build time (Wave 5 B3). Cached so
    repeated CompressReport construction doesn't hammer disk.
    """
    if not _BUILD_INFO_PATH.is_file():
        return None
    try:
        with _BUILD_INFO_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
    except OSError, json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def version_line() -> str:
    """Return the single-line version string for ``hankpdf --version``.

    Shape:
        hankpdf <version> (git <sha-7>, built <date>, python <py-version>)

    Docker image runs also include image <digest-7>. Dev installs just
    print the version since git/build-date aren't embedded.
    """
    extras: list[str] = []
    info = build_info()
    if info is not None:
        sha = str(info.get("git_sha", ""))
        if sha and sha != "unknown":
            extras.append(f"git {sha[:7]}")
        build_date = str(info.get("build_date", ""))
        if build_date and build_date != "unknown":
            extras.append(f"built {build_date}")
        digest = str(info.get("base_image_digest", ""))
        if digest and digest.startswith("sha256:"):
            extras.append(f"image {digest[len('sha256:') : len('sha256:') + 7]}")
    extras.append(f"python {platform.python_version()}")

    return f"hankpdf {__version__} ({', '.join(extras)})"
