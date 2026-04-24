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
#   docker pull python:3.14-slim-trixie && \
#     docker inspect --format='{{index .RepoDigests 0}}' python:3.14-slim-trixie
# or via the registry HTTP API; Dependabot's `docker` ecosystem will also
# propose bumps when a new sha appears.
#
# Switched from slim-bookworm to slim-trixie in Wave 5: bookworm ships
# qpdf 11.3.0, which has qpdf #1050 — a silent character-drop bug in
# 11.0.0–11.6.2 affecting /ID, XMP in encrypted files, bookmark names,
# form-field values, and encryption keys. SECURITY.md and docs/ROADMAP.md
# mandate qpdf >= 11.6.3; trixie ships qpdf 12.2.0. The build-time assert
# in the runtime stage fails the image build if the floor ever regresses.
FROM python:3.14-slim-trixie@sha256:5b3879b6f3cb77e712644d50262d05a7c146b7312d784a18eff7ff5462e77033 AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential autoconf automake libtool pkg-config \
        libleptonica-dev libpng-dev libjpeg-dev libtiff-dev \
        libwebp-dev zlib1g-dev ca-certificates git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# jbig2enc is unmaintained upstream; pin to a specific commit for
# reproducible builds. Source of truth: .github/versions.json
# (jbig2enc_commit). Both the Docker image and the Windows build read
# from the same JSON so a bump is a single line; ci.yml's
# versions-single-source job fails the build if this SHA ever appears
# outside .github/versions.json (grep-and-fail).
#
# The file is copied into the builder stage and parsed with a tiny
# python one-liner (python is already present since the base image is
# python:3.14-slim-*, so no extra tool is needed).
COPY .github/versions.json /tmp/versions.json
RUN JBIG2ENC_COMMIT="$(python3 -c 'import json; print(json.load(open("/tmp/versions.json"))["jbig2enc_commit"])')" \
    && case "${JBIG2ENC_COMMIT}" in \
        [0-9a-f]*) ;; \
        *) echo "FATAL: invalid jbig2enc commit SHA" >&2; exit 1 ;; \
    esac \
    && git clone https://github.com/agl/jbig2enc.git /src/jbig2enc \
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
FROM python:3.14-slim-trixie@sha256:5b3879b6f3cb77e712644d50262d05a7c146b7312d784a18eff7ff5462e77033 AS runtime

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
# libleptonica-dev is kept because Debian does not ship a standalone
# libleptonica runtime package; the dev metapackage pulls in libleptonica6
# without the bulk of build headers being used at runtime.
#
# Trixie still ships libtiff6 / libwebp7 / libjpeg62-turbo under the same
# names as bookworm. If any of these names drifts in a future release,
# apt's "unable to locate" error will be explicit and greppable.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-eng \
        qpdf \
        libleptonica-dev \
        libpng16-16 libjpeg62-turbo libtiff6 libwebp7 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# qpdf version floor (SECURITY.md + docs/ROADMAP.md): pre-11.6.3 has a
# silent character-drop bug (qpdf #1050) affecting /ID, XMP in encrypted
# files, bookmark names, form fields, and encryption keys. Fail the build
# if the installed qpdf ever regresses below the floor — far cheaper to
# catch in CI than in production on a signed document. The regex allows
# qpdf >= 11.6.3 (any 11.6.x where x>=3, any 11.7+, any 11.10+, any 12+).
RUN qpdf --version | head -1 | grep -qE '^qpdf version (1[2-9]|11\.(6\.[3-9]|6\.[1-9][0-9]+|[7-9]|1[0-9]+))' \
    || (echo "FATAL: qpdf floor >= 11.6.3 violated. See SECURITY.md / qpdf #1050." >&2; \
        qpdf --version >&2; exit 1)

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
