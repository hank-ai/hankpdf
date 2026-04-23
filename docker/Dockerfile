# HankPDF Docker image — Phase 0 skeleton.
#
# Phase 6 (packaging) fleshes this out. Current contents establish the
# structure: non-root user, baked native deps, slim base, healthcheck.
# Not yet runnable end-to-end — engine is still a stub.

FROM python:3.14-slim-bookworm AS base

ARG HANKPDF_VERSION=0.0.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Native deps.
# qpdf ≥ 11.6.3 is required (docs/ROADMAP.md T0.9); the bookworm-backports
# version should satisfy that. Phase 6 verifies + pins.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        qpdf \
        libleptonica-dev \
        build-essential \
        autoconf \
        automake \
        libtool \
        ca-certificates \
    # jbig2enc: vendor-build from pinned commit (unmaintained upstream).
    # Phase 6 replaces this with a proper vendored source tree + commit pin.
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user.
RUN groupadd --gid 1000 hankpdf \
    && useradd --uid 1000 --gid 1000 --create-home --shell /sbin/nologin hankpdf

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir /app

USER hankpdf
WORKDIR /data

HEALTHCHECK --interval=60s --timeout=5s --retries=2 \
    CMD hankpdf --version || exit 1

ENTRYPOINT ["hankpdf"]
CMD ["--help"]
