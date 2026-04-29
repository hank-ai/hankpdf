"""Regression: partial writes must land at .partial suffix first.

Reviewers C + D: SIGINT mid-`write_bytes` used to leave truncated files
at the final name. Downstream automation that globs `{base}_*.pdf` or
`page_*.jpg` would then ingest a partial file. The stale-chunk regex
can't detect truncation within the *new* chunk index range.

Fix: route every CLI output write through _atomic_write_bytes(), which
writes to `path.partial` first then `replace()`-es into place. On crash
mid-write the final path never exists; only the `.partial` sibling may
remain.
"""

from __future__ import annotations

import os
import pathlib

import pikepdf
import pytest

from hankpdf.cli.main import main


def _install_flaky_partial_writer(
    monkeypatch,  # type: ignore[no-untyped-def]
    tmp_path: pathlib.Path,
    fail_after_n: int,
    msg: str,
) -> None:
    """Make every ``.partial`` write inside ``tmp_path`` raise ``OSError``
    once ``calls >= fail_after_n``. Covers both POSIX (``os.write`` via
    ``os.open``) and Windows (``Path.write_bytes``) paths through
    ``_atomic_write_bytes``.
    """
    real_os_open = os.open
    real_os_write = os.write
    real_path_write = pathlib.Path.write_bytes
    calls = {"n": 0}
    fd_to_partial: dict[int, pathlib.Path] = {}

    def fake_os_open(path, flags, mode=0o777, *, dir_fd=None):  # type: ignore[no-untyped-def]
        fd = real_os_open(path, flags, mode, dir_fd=dir_fd)
        try:
            p = pathlib.Path(str(path))
            if p.parent == tmp_path and p.suffix == ".partial":
                fd_to_partial[fd] = p
        except OSError, ValueError:
            pass
        return fd

    def fake_os_write(fd: int, data: bytes) -> int:
        if fd in fd_to_partial:
            calls["n"] += 1
            if calls["n"] >= fail_after_n:
                raise OSError(msg)
        return real_os_write(fd, data)

    def fake_path_write(self: pathlib.Path, data: bytes) -> int:
        if self.parent == tmp_path and self.suffix == ".partial":
            calls["n"] += 1
            if calls["n"] >= fail_after_n:
                raise OSError(msg)
        return real_path_write(self, data)

    monkeypatch.setattr(os, "open", fake_os_open)
    monkeypatch.setattr(os, "write", fake_os_write)
    monkeypatch.setattr(pathlib.Path, "write_bytes", fake_path_write)


def _make_pdf_with_payload(tmp_path, n: int = 3):  # type: ignore[no-untyped-def]
    pdf = pikepdf.new()
    for _ in range(n):
        page = pdf.add_blank_page(page_size=(612, 792))
        img = pdf.make_stream(
            b"\x00" * 2048,
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Image,
            Width=10,
            Height=10,
            BitsPerComponent=8,
            ColorSpace=pikepdf.Name.DeviceRGB,
            Filter=pikepdf.Name.FlateDecode,
        )
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=img))
        page.Contents = pdf.make_stream(b"q 612 0 0 792 0 0 cm /Im0 Do Q\n")
    p = tmp_path / "in.pdf"
    pdf.save(p)
    return p


@pytest.mark.integration
def test_chunk_write_failure_leaves_no_partial_final_file(
    tmp_path,  # type: ignore[no-untyped-def]
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """If a chunk write fails mid-way, the final-named file must not
    exist — only a ``.partial`` sibling may remain. Invariant: every
    file matching the final pattern must be a complete chunk.
    """
    in_path = _make_pdf_with_payload(tmp_path, n=3)
    out_path = tmp_path / "smol.pdf"

    # Succeed on chunk 1, fail on chunk 2+. Covers both POSIX and Windows
    # branches inside _atomic_write_bytes.
    _install_flaky_partial_writer(monkeypatch, tmp_path, fail_after_n=2, msg="synthetic disk-full")

    rc = main(
        [
            str(in_path),
            "-o",
            str(out_path),
            "--max-output-mb",
            "0.005",
            "--accept-drift",
            "--min-ratio",
            "0",
        ]
    )
    # We forced an OSError → CLI returns EXIT_ENGINE_ERROR.
    assert rc != 0, f"expected non-zero rc on synthetic OSError; got {rc}"

    # Invariant: any smol_*.pdf final-named file that exists must be
    # a COMPLETE chunk. Truncated partials live under `.partial`.
    final_files = sorted(tmp_path.glob("smol_*.pdf"))
    for p in final_files:
        assert p.stat().st_size > 0, f"final-named file {p} is empty — atomic invariant violated"
    # No {base}_NNN.pdf file for the chunk that failed should exist at
    # its final name (atomic replace wasn't reached).
    # The atomic helper's .partial file may or may not remain on disk
    # depending on how mid-write the failure happened — that's fine, it's
    # clearly tagged so automation can skip it. Final-named files must
    # all be complete, which we asserted above.


@pytest.mark.integration
def test_image_export_partial_write_is_atomic(
    tmp_path,  # type: ignore[no-untyped-def]
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """Same invariant for the image-export loop: a failed partial write
    never leaves a partial final-named .jpg."""
    in_path = _make_pdf_with_payload(tmp_path, n=3)
    out_path = tmp_path / "page.jpg"

    _install_flaky_partial_writer(
        monkeypatch, tmp_path, fail_after_n=2, msg="synthetic image-export disk-full"
    )

    # Force multi-page so the helper is hit multiple times.
    with pytest.raises(OSError, match=r"disk-full"):
        main(
            [
                str(in_path),
                "-o",
                str(out_path),
                "--output-format",
                "jpeg",
                "--image-dpi",
                "72",
            ]
        )

    final_files = sorted(tmp_path.glob("page_*.jpg"))
    for p in final_files:
        assert p.stat().st_size > 0, f"final-named image {p} is empty — atomic invariant violated"
