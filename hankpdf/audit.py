"""Audit-sidecar support (Wave 5 / C2 + C3).

Builds the :class:`~hankpdf.types.BuildInfo` snapshot that pins every
CompressReport to the exact binary + native-dep versions that produced
it, and wires the process-wide correlation-id that stamps stderr lines
so an on-call can tie a log slice back to the structured report.

The BuildInfo is resolved once per process at first touch and cached —
``subprocess.run(["qpdf", "--version"])`` three times per invocation is
wasted work when the answers won't change between reports.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from functools import lru_cache

from hankpdf._version import __version__, build_info
from hankpdf.types import BuildInfo

_PROBE_TIMEOUT_SEC = 5


def _probe_tool_version(binary: str) -> str:
    """Run `<binary> --version` with a short timeout.

    Returns the first line of output, or "?" if the binary is missing /
    times out / exits unparseably. Never raises — BuildInfo must be
    constructable even on a machine where qpdf has been uninstalled
    between CLI startup and report emission.
    """
    resolved = shutil.which(binary)
    if resolved is None:
        return "?"
    try:
        out = subprocess.run(  # noqa: S603 — fixed arg0, no shell
            [resolved, "--version"],
            capture_output=True,
            check=False,
            timeout=_PROBE_TIMEOUT_SEC,
            text=True,
        )
    except subprocess.TimeoutExpired, OSError:
        return "?"
    first = (out.stdout or out.stderr).splitlines()[:1]
    if not first:
        return "?"
    return first[0].strip() or "?"


@lru_cache(maxsize=1)
def resolve_build_info() -> BuildInfo:
    """Return the BuildInfo for this process.

    Prefers fields from /etc/hankpdf/build-info.json (written by the
    Docker image at build time — B3). Falls back to at-runtime probes
    for any field the image didn't record, and to "?" when no probe
    answer is available.
    """
    info = build_info() or {}

    def _get(key: str, fallback: str) -> str:
        val = info.get(key)
        return str(val) if val not in (None, "", "unknown") else fallback

    return BuildInfo(
        version=_get("version", __version__),
        git_sha=_get("git_sha", "?"),
        build_date=_get("build_date", "?"),
        jbig2enc_commit=_get("jbig2enc_commit", "?"),
        qpdf_version=_get("qpdf_version", _probe_tool_version("qpdf")),
        tesseract_version=_get(
            "tesseract_version",
            _probe_tool_version("tesseract"),
        ),
        leptonica_version=_get("leptonica_version", "?"),
        python_version=_get("python_version", platform.python_version()),
        os_platform=_get("os_platform", platform.platform()),
        base_image_digest=_get("base_image_digest", "?"),
    )


# Module-level correlation id for the current process. Set by the CLI at
# startup (one per `hankpdf` invocation). Library callers that import
# `compress()` without going through the CLI get a fresh UUID4 per
# import via the CompressReport default factory — which is fine; their
# structured report always carries its own ID.
_process_correlation_id: str | None = None


def set_correlation_id(cid: str) -> None:
    """Record the correlation id for this process.

    Called once by the CLI before any stderr emission. warning_codes
    reads it to stamp every line. Rebinding is permitted (test suites
    drive main() multiple times per process); the last writer wins.
    """
    global _process_correlation_id  # noqa: PLW0603 — module-level state is the point
    _process_correlation_id = cid


def clear_correlation_id() -> None:
    """Unset the process correlation id. Used by tests to isolate runs."""
    global _process_correlation_id  # noqa: PLW0603 — module-level state is the point
    _process_correlation_id = None


def get_correlation_id() -> str | None:
    """Return the process correlation id if set, else None."""
    return _process_correlation_id
