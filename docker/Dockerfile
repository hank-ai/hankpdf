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

# Pin every apt package to the exact version currently in trixie/main
# (as of Wave 5). Pinning matters for reproducibility: without `=<version>`,
# a future security update can change the bits we build jbig2 against.
# Dependabot's docker ecosystem proposes bumps as trixie refreshes.
# Discover versions via `apt-cache policy <pkg>` inside the builder stage.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential=12.12 \
        autoconf=2.72-3.1 \
        automake=1:1.17-4 \
        libtool=2.5.4-4 \
        pkg-config=1.8.1-4 \
        libleptonica-dev=1.84.1-4 \
        libpng-dev=1.6.48-1+deb13u4 \
        libjpeg-dev=1:2.1.5-4 \
        libtiff-dev=4.7.0-3+deb13u2 \
        libwebp-dev=1.5.0-0.1 \
        zlib1g-dev=1:1.3.dfsg+really1.3.1-1+b1 \
        ca-certificates=20250419 \
        git=1:2.47.3-0+deb13u1 \
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

# Build metadata passed from the docker workflow. Defaults keep local
# `docker build` working without requiring every build-arg on the CLI;
# CI always sets them from the job context.
ARG HANKPDF_VERSION=0.0.0
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG BASE_IMAGE_DIGEST=sha256:5b3879b6f3cb77e712644d50262d05a7c146b7312d784a18eff7ff5462e77033

LABEL org.opencontainers.image.source="https://github.com/hank-ai/hankpdf"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.version="${HANKPDF_VERSION}"
LABEL org.opencontainers.image.revision="${GIT_SHA}"
LABEL org.opencontainers.image.created="${BUILD_DATE}"
LABEL org.opencontainers.image.title="HankPDF"
LABEL org.opencontainers.image.description="Aggressive, safety-first PDF shrinker for scanned documents"

# Surface the metadata at runtime too — `hankpdf --version` and
# `--doctor` read /etc/hankpdf/build-info.json (Group C). ENV vars
# also get set so shell scripts can `echo $HANKPDF_VERSION` without
# needing jq. The file is world-readable (no secrets).
ENV HANKPDF_VERSION=${HANKPDF_VERSION} \
    HANKPDF_GIT_SHA=${GIT_SHA} \
    HANKPDF_BUILD_DATE=${BUILD_DATE} \
    HANKPDF_BASE_IMAGE_DIGEST=${BASE_IMAGE_DIGEST}

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
        tesseract-ocr=5.5.0-1+b1 \
        tesseract-ocr-eng=1:4.1.0-2 \
        qpdf=12.2.0-1 \
        libleptonica-dev=1.84.1-4 \
        libpng16-16t64=1.6.48-1+deb13u4 \
        libjpeg62-turbo=1:2.1.5-4 \
        libtiff6=4.7.0-3+deb13u2 \
        libwebp7=1.5.0-0.1 \
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

# Copy versions.json into the runtime stage so build-info.json can
# reference the pinned jbig2enc commit at its canonical source (and
# `hankpdf --doctor` can echo it).
COPY .github/versions.json /etc/hankpdf/versions.json

# Write /etc/hankpdf/build-info.json with the canonical runtime
# identity. `hankpdf --version` and `--doctor` both read this file
# and include it in every CompressReport so an on-call can tie an
# output back to the exact image that produced it (Group C).
#
# The file ends up in the final image layer immediately before USER
# hankpdf, so the hankpdf user can read it (world-readable by default).
# qpdf / tesseract / python versions are probed at write time so a
# future apt refresh that changes them is reflected here without a
# Dockerfile edit.
RUN set -eu; \
    QPDF_VER="$(qpdf --version | head -n1 | awk '{print $3}')"; \
    TESS_VER="$(tesseract --version 2>&1 | head -n1 | awk '{print $2}')"; \
    PY_VER="$(python3 --version | awk '{print $2}')"; \
    LEP_VER="$(dpkg-query -W -f '${Version}' libleptonica-dev 2>/dev/null || echo unknown)"; \
    JBIG2_COMMIT="$(python3 -c 'import json; print(json.load(open("/etc/hankpdf/versions.json"))["jbig2enc_commit"])')"; \
    mkdir -p /etc/hankpdf; \
    printf '%s\n' '{' \
        "  \"version\": \"${HANKPDF_VERSION}\"," \
        "  \"git_sha\": \"${GIT_SHA}\"," \
        "  \"build_date\": \"${BUILD_DATE}\"," \
        "  \"base_image_digest\": \"${BASE_IMAGE_DIGEST}\"," \
        "  \"jbig2enc_commit\": \"${JBIG2_COMMIT}\"," \
        "  \"qpdf_version\": \"${QPDF_VER}\"," \
        "  \"tesseract_version\": \"${TESS_VER}\"," \
        "  \"leptonica_version\": \"${LEP_VER}\"," \
        "  \"python_version\": \"${PY_VER}\"," \
        "  \"os_platform\": \"debian-trixie\"" \
        '}' > /etc/hankpdf/build-info.json; \
    cat /etc/hankpdf/build-info.json

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
# Copy both packages: `hankpdf` is the canonical post-rename package;
# `pdf_smasher` is the deprecation shim for one cycle (re-exports the
# public API from `hankpdf` so legacy `from pdf_smasher import compress`
# keeps working through v0.2.x). hatchling needs both directories
# present in the build context because pyproject.toml's wheel target
# lists them both. Removal of the shim is scheduled for v0.3.0.
COPY hankpdf /app/hankpdf
COPY pdf_smasher /app/pdf_smasher
RUN pip install --no-cache-dir "uv==0.9.9" \
    && uv sync --frozen --no-dev --no-editable \
    && rm -rf /root/.cache/uv
ENV PATH="/app/.venv/bin:${PATH}"

# Entrypoint wrapper: prints an actionable hint when /data is not
# writable by uid 1000 before invoking hankpdf. See E1 + docker/README.md.
COPY docker/entrypoint.sh /usr/local/bin/hankpdf-entrypoint
RUN chmod 755 /usr/local/bin/hankpdf-entrypoint

USER hankpdf
WORKDIR /data

ENTRYPOINT ["/usr/local/bin/hankpdf-entrypoint"]
CMD ["--help"]
