# HankPDF Docker image — multi-stage build with vendored jbig2enc.
#
# Stage 1 (builder) compiles jbig2enc from a pinned commit against a matching
# Leptonica. Stage 2 (runtime) is a slim image that copies only the jbig2
# binary and installs runtime-only shared libs — no compilers, no -dev headers.
#
# Native deps baked in: Tesseract 5 + English tessdata, qpdf, jbig2enc.
# Image is non-root (uid/gid 1000) and runs `hankpdf` by default.

# -----------------------------------------------------------------------------
# Stage 1 — builder
# -----------------------------------------------------------------------------
# Base image pinned by digest for reproducibility (supply-chain hygiene).
# Both stages MUST share the same digest so the runtime libc matches what we
# linked jbig2 against. Regenerate via:
#   docker pull python:3.14-slim-bookworm && \
#     docker inspect --format='{{index .RepoDigests 0}}' python:3.14-slim-bookworm
# or via the registry HTTP API; Dependabot's `docker` ecosystem will also
# propose bumps when a new sha appears.
FROM python:3.14-slim-bookworm@sha256:2e256d0381371566ed96980584957ed31297f437569b79b0e5f7e17f2720e53a AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential autoconf automake libtool pkg-config \
        libleptonica-dev libpng-dev libjpeg-dev libtiff-dev \
        libwebp-dev zlib1g-dev ca-certificates git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# jbig2enc is unmaintained upstream; pin to a specific commit for
# reproducible builds. Verified latest main SHA as of 2026-04-23.
ARG JBIG2ENC_COMMIT=e3fcf0210efa4f644e458a51616d86098d47d8da
RUN git clone https://github.com/agl/jbig2enc.git /src/jbig2enc \
    && cd /src/jbig2enc \
    && git checkout "${JBIG2ENC_COMMIT}" \
    && ./autogen.sh \
    && ./configure --prefix=/usr/local \
    && make -j"$(nproc)" \
    && make install \
    && strip /usr/local/bin/jbig2

# -----------------------------------------------------------------------------
# Stage 2 — runtime
# -----------------------------------------------------------------------------
# Same base + digest as the builder — see note above. If you regenerate the
# digest, update BOTH FROM lines in lockstep.
FROM python:3.14-slim-bookworm@sha256:2e256d0381371566ed96980584957ed31297f437569b79b0e5f7e17f2720e53a AS runtime

ARG HANKPDF_VERSION=0.0.0

LABEL org.opencontainers.image.source="https://github.com/hank-ai/hankpdf"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.version="${HANKPDF_VERSION}"
LABEL org.opencontainers.image.title="HankPDF"
LABEL org.opencontainers.image.description="Aggressive, safety-first PDF shrinker for scanned documents"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Runtime-only deps (no -dev packages, no compilers).
# libleptonica-dev is kept because bookworm does not ship a standalone
# libleptonica runtime package; the dev metapackage pulls in libleptonica6
# without the bulk of build headers being used at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-eng \
        qpdf \
        libleptonica-dev \
        libpng16-16 libjpeg62-turbo libtiff6 libwebp7 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy the vendored jbig2 binary from the builder stage.
COPY --from=builder /usr/local/bin/jbig2 /usr/local/bin/jbig2

# Non-root runtime user.
RUN groupadd --gid 1000 hankpdf \
    && useradd --uid 1000 --gid 1000 --create-home --shell /sbin/nologin hankpdf

# Install HankPDF with uv + frozen lockfile.
#
# `pip install /app` floats every transitive (wheel index picks the newest
# compatible build each image build) — a supply-chain hole the same shape
# as floating action tags. `uv sync --frozen` refuses to run if uv.lock is
# out of sync with pyproject.toml, so the image ships EXACTLY what CI
# tested. --no-dev drops pytest/ruff/mypy from the runtime image.
#
# uv itself is pulled via `pip install uv`; we pin the uv version so this
# is a single reproducibility surface (bumped by Dependabot pip ecosystem).
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE NOTICE /app/
COPY pdf_smasher /app/pdf_smasher
RUN pip install --no-cache-dir "uv==0.9.9" \
    && uv sync --frozen --no-dev --no-editable \
    && rm -rf /root/.cache/uv
ENV PATH="/app/.venv/bin:${PATH}"

USER hankpdf
WORKDIR /data

HEALTHCHECK --interval=60s --timeout=5s --retries=2 \
    CMD hankpdf --version || exit 1

ENTRYPOINT ["hankpdf"]
CMD ["--help"]
