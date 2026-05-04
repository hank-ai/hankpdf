"""Smoke test: the extracted gate module exposes the expected surface."""

from __future__ import annotations


def test_per_page_gate_module_exports() -> None:
    from hankpdf.engine.per_page_gate import PerPageGateResult, run_per_page_gate

    assert hasattr(run_per_page_gate, "__call__")
    fields = {f.name for f in PerPageGateResult.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    assert {"mrc_worthy", "whole_doc_passthrough", "warnings"} <= fields
