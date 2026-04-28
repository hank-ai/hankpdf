"""Deprecation shim — ``pdf_smasher`` was renamed to ``hankpdf`` in v0.2.0.

The PyPI dist + import package were both renamed to consolidate around
the product brand (HankPDF). This module re-exports the public API from
``hankpdf`` and emits a :class:`DeprecationWarning` so existing
``from pdf_smasher import compress`` callers get a one-cycle soft
landing.

Scheduled for removal in v0.3.0. Migrate now:

.. code-block:: python

    # before
    from pdf_smasher import compress, CompressOptions
    from pdf_smasher.types import CompressReport

    # after
    from hankpdf import compress, CompressOptions
    from hankpdf.types import CompressReport

The yanked ``pdf-smasher`` PyPI distribution still installs at the
exact pin (``pip install pdf-smasher==0.1.0``) but resolves no further
versions. The new distribution is ``pip install hankpdf``.
"""

from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "The `pdf_smasher` import package was renamed to `hankpdf` in v0.2.0. "
    "Update your imports: `from hankpdf import compress, CompressOptions`. "
    "This shim will be removed in v0.3.0.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export the full public API. Everything in `hankpdf` becomes
# accessible via `pdf_smasher` so legacy callers keep working for one
# release cycle.
from hankpdf import *  # noqa: F403, E402
from hankpdf import (  # noqa: E402
    BuildInfo,
    CompressOptions,
    CompressReport,
    TriageReport,
    VerifierResult,
    __engine_version__,
    __version__,
    compress,
    compress_stream,
    triage,
)

__all__ = [
    "BuildInfo",
    "CompressOptions",
    "CompressReport",
    "TriageReport",
    "VerifierResult",
    "__engine_version__",
    "__version__",
    "compress",
    "compress_stream",
    "triage",
]
