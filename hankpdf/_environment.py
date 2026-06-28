"""Native-dependency boot check for HankPDF.

This module detects the host's installed Tesseract / qpdf / jbig2enc /
OpenJPEG (via Pillow) versions and compares them against the floors
documented in ``docs/SPEC.md`` section 1.2. The floors below are the
lowest versions HankPDF has been validated against; older builds
frequently have known crashes or incorrect output for the workloads we
run.

Public surface
--------------

``get_environment_report()``
    Cached probe. Returns an :class:`EnvironmentReport` describing what
    we found and any :class:`EnvFailure` records. Pure data; never
    raises for missing/old binaries.

``assert_environment_ready()``
    Convenience wrapper around :func:`get_environment_report` that
    raises :class:`hankpdf.exceptions.EnvironmentError` (with
    ``.failures`` populated) when any floor is violated. This is the
    function the public :func:`hankpdf.compress` entry point calls.

``HANKPDF_SKIP_ENV_CHECK=1``
    Short-circuits the probe: returns an empty report with no failures.
    Used by tests that intentionally run under stub binaries (CI
    matrices, sandboxed unit tests).

Design notes
------------

* Subprocess invocation uses :class:`subprocess.Popen` (not
  :func:`subprocess.run`) so we can cap stdout reads at 16 KiB:
  prevents a misbehaving binary that prints unbounded output from
  blowing memory during the probe. A 5-second wall-clock timeout per
  tool is enforced via :meth:`Popen.communicate`.

* The OpenJPEG probe is in-process: it asks Pillow to encode a tiny
  RGB tile to JPEG2000. This avoids shelling out to a separate binary
  and gives us a definitive "this Pillow can write j2k" answer, which
  is what the compress() pipeline actually needs.

* jbig2enc absence is *warning-only* (it produces an
  :class:`EnvFailure` with ``reason="missing"`` but is filtered out of
  the floor-violations list that triggers
  :class:`EnvironmentError`). The encode pipeline degrades to flate
  (~6x compression vs. ~50x with jbig2enc) but still produces correct
  output.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from functools import cache
from typing import TYPE_CHECKING, Final

from hankpdf.exceptions import EnvironmentError as _EnvError

if TYPE_CHECKING:
    from collections.abc import Callable

_log = logging.getLogger(__name__)

# Floors. Bumping these is a coordinated change with docs/SPEC.md
# section 1.2 and the Dockerfile's apt-get pin lines.
TESSERACT_FLOOR: Final = "5.0.0"
QPDF_FLOOR: Final = "11.6.3"
OPENJPEG_FLOOR: Final = "2.5.4"  # Final OpenJPEG release. Probed via Pillow.

# Subprocess safety knobs.
_PROBE_TIMEOUT_SECONDS: Final[float] = 5.0
_PROBE_STDOUT_CAP_BYTES: Final[int] = 16 * 1024

# Matches X.Y or X.Y.Z anywhere in a string. The leading word boundary
# keeps us from snagging the trailing digit of "leptonica-1.84.1" when
# scanning ``tesseract --version`` output (we read line-by-line, so
# this only matters as belt-and-suspenders).
_VERSION_RE = re.compile(r"\b(\d+\.\d+(?:\.\d+)?)\b")


@dataclass(frozen=True, slots=True)
class EnvFailure:
    """Single floor-violation or missing-tool record."""

    component: str
    """e.g. ``"tesseract"``, ``"qpdf"``, ``"jbig2enc"``, ``"openjpeg"``."""

    reason: str
    """``"missing"``, ``"below-floor"``, ``"probe-failed"``, ``"unparseable"``."""

    found: str | None = None
    """Version string we detected (``None`` if missing/unparseable)."""

    required: str | None = None
    """Floor the component was compared against."""

    install_hint: str | None = None
    """Human-readable install/upgrade hint."""


@dataclass(frozen=True, slots=True)
class EnvironmentReport:
    """Full environment probe result. Frozen - safe to share across threads."""

    python_version: str
    platform: str
    tesseract: str | None
    qpdf: str | None
    jbig2enc: str | None
    openjpeg_via_pillow: str | None
    pdfium_revision: str | None
    pillow_max_image_pixels: int
    failures: tuple[EnvFailure, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Version parsers - small, pure, easy to unit-test.
# ---------------------------------------------------------------------------


def _first_version_in(text: str) -> str | None:
    """Return the first ``X.Y[.Z]`` token in ``text`` or ``None``."""
    if not text:
        return None
    match = _VERSION_RE.search(text)
    return match.group(1) if match else None


def parse_tesseract_version(raw: str) -> str | None:
    """Parse the version off ``tesseract --version`` output.

    Real output starts with ``tesseract 5.3.4``; later lines list
    ``leptonica-1.84.1`` etc. We only look at the first non-empty line
    so a leptonica version can never masquerade as the tesseract one.
    """
    if not raw:
        return None
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        return _first_version_in(line)
    return None


def parse_qpdf_version(raw: str) -> str | None:
    """Parse the version off ``qpdf --version`` output.

    Real output: ``qpdf version 11.6.4``.
    """
    if not raw:
        return None
    for line in raw.splitlines():
        if "qpdf" in line.lower():
            v = _first_version_in(line)
            if v:
                return v
    return _first_version_in(raw)


def parse_jbig2_version(raw: str) -> str | None:
    """Parse the version off ``jbig2 -V`` (or ``--version``) output.

    jbig2enc's CLI is inconsistent across distros: some print
    ``jbig2enc 0.29``, some print just ``0.29``, some go to stderr.
    We accept any ``X.Y[.Z]`` token.
    """
    return _first_version_in(raw or "")


# ---------------------------------------------------------------------------
# Floor comparison. Plain tuple-of-ints; we never see non-numeric
# components from these tools (no "-rc1" tails in real output).
# ---------------------------------------------------------------------------


def _version_tuple(v: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in v.split("."):
        # Strip any non-digit suffix defensively (e.g. "11.6.3-1").
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _meets_floor(found: str, floor: str) -> bool:
    return _version_tuple(found) >= _version_tuple(floor)


# ---------------------------------------------------------------------------
# Subprocess probe with bounded reads + timeout.
# ---------------------------------------------------------------------------


def _probe(
    tool: str,
    parser: Callable[[str], str | None],
) -> tuple[str | None, str | None]:
    """Invoke ``<tool> --version`` and parse the first version token.

    Returns ``(version, error_reason)`` where exactly one is non-None
    on success/failure. ``error_reason`` is one of ``"missing"``,
    ``"probe-failed"``, ``"unparseable"``.
    """
    if shutil.which(tool) is None:
        return (None, "missing")

    try:
        proc = subprocess.Popen(  # noqa: S603 - tool name is from a fixed allowlist
            [tool, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, ValueError):
        _log.debug("env probe: failed to spawn %s", tool, exc_info=True)
        return (None, "probe-failed")

    try:
        stdout_b, stderr_b = proc.communicate(timeout=_PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _log.debug("env probe: %s --version timed out", tool)
        with contextlib.suppress(Exception):
            proc.kill()
            proc.wait(timeout=1.0)
        return (None, "probe-failed")
    except Exception:  # noqa: BLE001 - probe must never raise to caller
        _log.debug("env probe: %s communicate failed", tool, exc_info=True)
        return (None, "probe-failed")

    # Bound the buffers we feed into the parser. Some tools (rare, but
    # seen in the wild - e.g. broken jbig2 builds that dump a banner
    # blob) emit way more than 16 KiB on --version; truncating keeps
    # the regex search predictable. Cap BEFORE decoding so a flood of
    # bytes can't blow up the decode step either.
    stdout_b = (stdout_b or b"")[:_PROBE_STDOUT_CAP_BYTES]
    stderr_b = (stderr_b or b"")[:_PROBE_STDOUT_CAP_BYTES]

    # Decode with explicit UTF-8 + errors="replace" to stay safe on
    # Linux hosts whose default locale is e.g. POSIX/ASCII (where
    # text=True would raise UnicodeDecodeError on any non-ASCII byte
    # in the version banner).
    raw_bytes = stdout_b or stderr_b
    raw = raw_bytes.decode("utf-8", errors="replace")
    version = parser(raw)
    if version is None:
        return (None, "unparseable")
    return (version, None)


# ---------------------------------------------------------------------------
# In-process probes (no subprocess).
# ---------------------------------------------------------------------------


def _probe_openjpeg() -> tuple[str | None, str | None]:
    """Verify Pillow can encode JPEG2000.

    Returns ``(found, error_reason)``. ``found`` is the string
    ``"available"`` (we cannot extract a precise OpenJPEG version
    through the Pillow API; what matters is whether encode works).
    Failure modes:

    * ``"missing"`` - Pillow has no JPEG2000 plugin compiled in.
    * ``"probe-failed"`` - encode raised an exception.
    """
    try:
        from PIL import Image, features  # noqa: PLC0415 - probe lives here
    except ImportError:
        return (None, "missing")

    try:
        if not features.check("jpg_2000"):
            return (None, "missing")
    except Exception:  # noqa: BLE001 - Pillow API drift fallback
        # ``features.check`` raises ``ValueError`` for unknown names on
        # very old Pillows. Fall through to the encode probe.
        _log.debug("env probe: Pillow features.check raised", exc_info=True)

    try:
        img = Image.new("RGB", (8, 8), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="JPEG2000")
        if not buf.getvalue():
            return (None, "probe-failed")
    except Exception:  # noqa: BLE001 - probe must never raise to caller
        _log.debug("env probe: JPEG2000 encode failed", exc_info=True)
        return (None, "probe-failed")

    return ("available", None)


def _pillow_max() -> int:
    try:
        import PIL.Image  # noqa: PLC0415 - probe-time import
    except ImportError:
        return 0
    try:
        return int(PIL.Image.MAX_IMAGE_PIXELS or 0)
    except Exception:  # noqa: BLE001 - probe must never raise
        return 0


def _platform_string() -> str:
    """Build a platform descriptor without invoking subprocess.

    ``platform.platform()`` may shell out to ``uname -p`` on macOS to
    fill the processor field, which would tangle with our subprocess
    mocks during tests. Compose the same useful fields directly.
    """
    try:
        return f"{platform.system()} {platform.release()} {platform.machine()}"
    except Exception:  # noqa: BLE001 - never let the probe raise
        return sys.platform


def _pdfium_rev() -> str | None:
    try:
        import pypdfium2  # noqa: PLC0415
    except ImportError:
        return None
    try:
        rev = getattr(pypdfium2, "V_PDFIUM", None)
        if rev:
            return str(rev)
        version = getattr(pypdfium2, "__version__", None)
        return None if version is None else str(version)
    except Exception:  # noqa: BLE001 - never let the probe raise
        return None


# ---------------------------------------------------------------------------
# Install hints - short, copy-pasteable, OS-specific where possible.
# ---------------------------------------------------------------------------


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _install_hint_tesseract() -> str:
    if _is_macos():
        return "Install with: brew install tesseract"
    if _is_linux():
        return (
            "Install with: apt-get install tesseract-ocr (Debian/Ubuntu) "
            "or dnf install tesseract (RHEL/Fedora)"
        )
    return "Install Tesseract >= 5.0.0 from https://github.com/tesseract-ocr/tesseract"


def _install_hint_qpdf() -> str:
    if _is_macos():
        return "Install with: brew install qpdf"
    if _is_linux():
        return (
            "Install qpdf >= 11.6.3 from https://github.com/qpdf/qpdf/releases "
            "(system packages may be outdated)"
        )
    return "Install qpdf >= 11.6.3 from https://github.com/qpdf/qpdf/releases"


def _install_hint_jbig2enc() -> str:
    if _is_macos():
        return "Install with: brew install jbig2enc (or build from https://github.com/agl/jbig2enc)"
    if _is_linux():
        return "Build from https://github.com/agl/jbig2enc - distro packages are rare"
    return "Install jbig2enc from https://github.com/agl/jbig2enc"


def _install_hint_openjpeg() -> str:
    return (
        "Reinstall Pillow with JPEG2000 support: pip install --force-reinstall Pillow "
        "(ensure libopenjp2 >= 2.5.4 is on PATH at install time)"
    )


# ---------------------------------------------------------------------------
# Public probe entry points.
# ---------------------------------------------------------------------------


def _check_tesseract(failures: list[EnvFailure]) -> str | None:
    ver, err = _probe("tesseract", parse_tesseract_version)
    if err == "missing":
        failures.append(
            EnvFailure(
                component="tesseract",
                reason="missing",
                required=TESSERACT_FLOOR,
                install_hint=_install_hint_tesseract(),
            )
        )
    elif err is not None:
        failures.append(
            EnvFailure(
                component="tesseract",
                reason=err,
                required=TESSERACT_FLOOR,
                install_hint=_install_hint_tesseract(),
            )
        )
    elif ver is not None and not _meets_floor(ver, TESSERACT_FLOOR):
        failures.append(
            EnvFailure(
                component="tesseract",
                reason="below-floor",
                found=ver,
                required=TESSERACT_FLOOR,
                install_hint=_install_hint_tesseract(),
            )
        )
    return ver


def _check_qpdf(failures: list[EnvFailure]) -> str | None:
    ver, err = _probe("qpdf", parse_qpdf_version)
    if err == "missing":
        failures.append(
            EnvFailure(
                component="qpdf",
                reason="missing",
                required=QPDF_FLOOR,
                install_hint=_install_hint_qpdf(),
            )
        )
    elif err is not None:
        failures.append(
            EnvFailure(
                component="qpdf",
                reason=err,
                required=QPDF_FLOOR,
                install_hint=_install_hint_qpdf(),
            )
        )
    elif ver is not None and not _meets_floor(ver, QPDF_FLOOR):
        failures.append(
            EnvFailure(
                component="qpdf",
                reason="below-floor",
                found=ver,
                required=QPDF_FLOOR,
                install_hint=_install_hint_qpdf(),
            )
        )
    return ver


def _check_jbig2(failures: list[EnvFailure]) -> str | None:
    ver, err = _probe("jbig2", parse_jbig2_version)
    if err == "missing":
        failures.append(
            EnvFailure(
                component="jbig2enc",
                reason="missing",
                install_hint=_install_hint_jbig2enc(),
            )
        )
    elif err is not None:
        failures.append(
            EnvFailure(
                component="jbig2enc",
                reason=err,
                install_hint=_install_hint_jbig2enc(),
            )
        )
    return ver


def _check_openjpeg(failures: list[EnvFailure]) -> str | None:
    ver, err = _probe_openjpeg()
    if err is not None:
        failures.append(
            EnvFailure(
                component="openjpeg",
                reason=err,
                required=OPENJPEG_FLOOR,
                install_hint=_install_hint_openjpeg(),
            )
        )
    return ver


@cache
def get_environment_report() -> EnvironmentReport:
    """Probe the environment once per process. Cached.

    Setting ``HANKPDF_SKIP_ENV_CHECK=1`` short-circuits to an empty
    report - used by tests that intentionally run with stub binaries.
    """
    if os.environ.get("HANKPDF_SKIP_ENV_CHECK") == "1":
        return EnvironmentReport(
            python_version=platform.python_version(),
            platform=_platform_string(),
            tesseract=None,
            qpdf=None,
            jbig2enc=None,
            openjpeg_via_pillow=None,
            pdfium_revision=None,
            pillow_max_image_pixels=_pillow_max(),
            failures=(),
        )

    failures: list[EnvFailure] = []
    tess_ver = _check_tesseract(failures)
    qpdf_ver = _check_qpdf(failures)
    jbig2_ver = _check_jbig2(failures)
    openjpeg_ver = _check_openjpeg(failures)

    return EnvironmentReport(
        python_version=platform.python_version(),
        platform=_platform_string(),
        tesseract=tess_ver,
        qpdf=qpdf_ver,
        jbig2enc=jbig2_ver,
        openjpeg_via_pillow=openjpeg_ver,
        pdfium_revision=_pdfium_rev(),
        pillow_max_image_pixels=_pillow_max(),
        failures=tuple(failures),
    )


# Components that, if absent or below floor, should raise. jbig2enc is
# absent from this set: missing it is logged but not fatal.
_BLOCKING_COMPONENTS: frozenset[str] = frozenset({"tesseract", "qpdf", "openjpeg"})


def _is_blocking_failure(f: EnvFailure) -> bool:
    return f.component in _BLOCKING_COMPONENTS


def format_failure_message(failures: tuple[EnvFailure, ...]) -> str:
    """Render a multi-line, human-readable failure summary."""
    if not failures:
        return "Environment OK."
    lines: list[str] = ["HankPDF environment check failed:"]
    for f in failures:
        if f.reason == "missing":
            lines.append(f"  - {f.component}: not found on PATH")
        elif f.reason == "below-floor":
            lines.append(f"  - {f.component}: found {f.found}, requires >= {f.required}")
        elif f.reason == "unparseable":
            lines.append(f"  - {f.component}: could not parse version output")
        else:
            lines.append(f"  - {f.component}: {f.reason}")
        if f.install_hint:
            lines.append(f"      {f.install_hint}")
    lines.append("")
    lines.append("Run `hankpdf doctor` for the full diagnostic report.")
    lines.append(
        "Set HANKPDF_SKIP_ENV_CHECK=1 to bypass this check (only for tests with stub binaries)."
    )
    return "\n".join(lines)


def assert_environment_ready() -> EnvironmentReport:
    """Raise :class:`EnvironmentError` if any blocking floor is violated.

    Returns the :class:`EnvironmentReport` on success so callers can
    log structured fields.
    """
    report = get_environment_report()
    blocking = tuple(f for f in report.failures if _is_blocking_failure(f))
    if blocking:
        msg = format_failure_message(blocking)
        err = _EnvError(msg)
        err.failures = blocking
        raise err
    return report


__all__ = [
    "OPENJPEG_FLOOR",
    "QPDF_FLOOR",
    "TESSERACT_FLOOR",
    "EnvFailure",
    "EnvironmentReport",
    "assert_environment_ready",
    "format_failure_message",
    "get_environment_report",
    "parse_jbig2_version",
    "parse_qpdf_version",
    "parse_tesseract_version",
]
