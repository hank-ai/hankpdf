"""Small text-formatting helpers used across the CLI and the library.

Kept tiny and dependency-free so both the engine code (which should
stay import-light) and the CLI layer can share them without cycles.
"""

from __future__ import annotations


def format_page_list_short(ids: list[int], limit: int = 10) -> str:
    """Format a list of 1-indexed page numbers compactly for error messages.

    When out_of_range lists grow to hundreds of thousands of entries (e.g.
    from `--pages 1-1000000` on a 10-page PDF), stringifying the whole
    list produces multi-MB stderr spew. Cap at ``limit`` entries plus an
    explicit ``(+N more)`` suffix so logs stay readable.

    Sorted for deterministic output. Non-destructive: the caller's list
    is not mutated.
    """
    if limit < 1:
        msg = f"limit must be >= 1 (got {limit})"
        raise ValueError(msg)
    sorted_ids = sorted(ids)
    if len(sorted_ids) <= limit:
        return f"[{', '.join(str(n) for n in sorted_ids)}]"
    head = sorted_ids[:limit]
    remaining = len(sorted_ids) - limit
    return f"[{', '.join(str(n) for n in head)}, ... (+{remaining} more)]"
