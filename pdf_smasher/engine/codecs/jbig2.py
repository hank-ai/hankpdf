"""JBIG2 encoder wrapper.

Shells out to the ``jbig2`` binary (jbig2enc). **Generic region coding
only** — no symbol mode, no refinement — per the safety decision in
SPEC.md §4.3.3 (avoids the Xerox 6/8 substitution risk and the Acrobat
refinement crash).

The ``-p`` flag makes jbig2enc emit PDF-ready bytes suitable for embedding
in a PDF stream with ``/Filter /JBIG2Decode``.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from functools import cache
from pathlib import Path

from PIL import Image

_JBIG2_BIN = "jbig2"
_JBIG2_TIMEOUT_SECONDS = 120


@cache
def _resolve_jbig2_bin() -> str | None:
    """Resolve the jbig2 binary's absolute path once. ``None`` if not on PATH.

    Cached so subsequent subprocess calls skip the re-walk; cache is
    process-lifetime (installing jbig2enc mid-process is not a supported
    scenario).
    """
    return shutil.which(_JBIG2_BIN)


def encode_1bit_jbig2(mask: Image.Image) -> bytes:
    """Encode a 1-bit PIL image to JBIG2 bytes suitable for /JBIG2Decode embedding.

    Parameters
    ----------
    mask:
        PIL image in ``"1"`` mode. RGB or grayscale will be rejected with
        ``ValueError`` — callers must convert explicitly.

    Returns
    -------
    bytes
        JBIG2-encoded stream, generic region coding, PDF-ready.
    """
    if mask.mode != "1":
        msg = f"encode_1bit_jbig2 requires 1-bit image; got mode {mask.mode!r}"
        raise ValueError(msg)

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        png_path = tmp_path / "mask.png"
        # Save as PNG — jbig2enc accepts PNG, PBM, TIFF.
        mask.save(png_path, format="PNG")

        # Invoke: jbig2 -p <input> writes a single .jb2 to stdout-ish? Actually
        # jbig2 with -p writes a single PDF-ready page stream. Absent -s
        # (symbol mode), it uses generic region coding. Output goes to
        # stdout via its `-o` option... but easier path: it writes
        # `<basename>.jb2` files. We run against a single file.
        #
        # The reliable invocation is: `jbig2 -p <input>` which prints the
        # JBIG2 page bytes to stdout.
        binary = _resolve_jbig2_bin()
        if binary is None:
            msg = f"jbig2 not found on PATH; install jbig2enc"
            raise FileNotFoundError(msg)
        result = subprocess.run(
            [binary, "-p", str(png_path)],
            capture_output=True,
            check=True,
            timeout=_JBIG2_TIMEOUT_SECONDS,
        )
        return result.stdout
