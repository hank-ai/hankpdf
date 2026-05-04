"""Per-page MRC gate.

Decides which pages go through the MRC pipeline vs verbatim passthrough,
based on the per-page image-byte fraction. Lifted out of
``hankpdf/__init__.py`` in v0.3.0 so the 1455-line module stops growing.

The gate is a pre-filter, not a verifier. Conservative biases (no nested
Form XObject walk, no parent-inherited /Resources) push toward MRC, the
safe direction. See SPEC.md §4.1b.
"""

from __future__ import annotations

from dataclasses import dataclass

from hankpdf.engine.page_classifier import score_pages_for_mrc
from hankpdf.types import CompressOptions, TriageReport


@dataclass(frozen=True, slots=True)
class PerPageGateResult:
    """Outcome of the per-page MRC gate.

    Fields:
        mrc_worthy: per-page bool. True → run MRC; False → verbatim copy.
        whole_doc_passthrough: True iff no page met the threshold AND
            no override (re_ocr / strip_text_layer / legal_codec_profile /
            verify) forces the full pipeline. Caller returns input bytes
            unchanged when this is set.
        warnings: structured warning codes the caller can surface. Today
            the gate only emits ``passthrough-no-image-content`` for the
            whole-doc-passthrough path; the partial-passthrough
            ``pages-skipped-verbatim-N`` warning is appended by the
            caller after worker dispatch (it depends on which workers
            actually emitted verbatim verdicts, not just gate flags).
    """

    mrc_worthy: tuple[bool, ...]
    whole_doc_passthrough: bool
    warnings: tuple[str, ...]


def _force_full_pipeline(options: CompressOptions) -> bool:
    """Mirror the existing interlock from compress(): any of these flags
    forces every page through the MRC pipeline.

    Disable conditions (kept in lockstep with the worker fast-path
    assertion at hankpdf/__init__.py:411):
      --re-ocr            → Tesseract on every page; verbatim incompatible.
      --strip-text-layer  → no-text-layer output; verbatim preserves it.
      --legal-mode        → CCITT G4 archival profile re-encodes every page;
                            verbatim copy would defeat the legal codec
                            guarantee.
      --verify (skip_verify=False) → verbatim pages would feed synthetic
                            PageVerdict values into _VerifierAggregator
                            and pollute the aggregate ssim/lev/digit
                            metrics on partial-passthrough runs.
    """
    return bool(
        options.re_ocr
        or options.strip_text_layer
        or options.legal_codec_profile
        or not options.skip_verify
    )


def run_per_page_gate(
    input_data: bytes,
    triage: TriageReport,
    options: CompressOptions,
) -> PerPageGateResult:
    """Score pages, decide passthrough vs MRC, return the gate result.

    The caller threads ``mrc_worthy`` into ``_WorkerInput.mrc_worthy``
    per page. If ``whole_doc_passthrough`` is True, the caller emits the
    ``passthrough-no-image-content`` warning and returns input bytes
    unchanged.

    Note: ``score_pages_for_mrc`` takes raw bytes and re-opens via
    pikepdf internally. Reusing the caller's already-open Pdf would be
    a different refactor; staying byte-based preserves call semantics.
    """
    if _force_full_pipeline(options):
        return PerPageGateResult(
            mrc_worthy=tuple([True] * triage.pages),
            whole_doc_passthrough=False,
            warnings=(),
        )

    flags = score_pages_for_mrc(
        input_data,
        password=options.password,
        min_image_byte_fraction=options.min_image_byte_fraction,
    )

    # Defensive: if the classifier disagreed with triage on the page count
    # (qpdf-repaired inputs, or a future pikepdf upgrade that changes the
    # iteration semantics), surface the contract drift now rather than
    # IndexError-ing deep in the per-page loop. Coerce to triage's count
    # (the source of truth for everything downstream).
    if len(flags) != triage.pages:
        flags = (list(flags) + [True] * triage.pages)[: triage.pages]

    if not any(flags):
        return PerPageGateResult(
            mrc_worthy=tuple(flags),
            whole_doc_passthrough=True,
            warnings=("passthrough-no-image-content",),
        )

    # Partial passthrough: don't emit pages-skipped-verbatim-N here.
    # The caller appends that warning later, after worker dispatch,
    # using the actual _verbatim_pages set (post-worker-execution).
    return PerPageGateResult(
        mrc_worthy=tuple(flags),
        whole_doc_passthrough=False,
        warnings=(),
    )
