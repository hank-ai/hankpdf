# Docker

HankPDF is published as a multi-arch Docker image on GHCR. All native
dependencies (Tesseract, qpdf, jbig2enc) are baked in — zero host setup.

## Pull

```bash
docker pull ghcr.io/hank-ai/hankpdf:latest
```

## Run

```bash
# Linux
docker run --rm -u "$(id -u):$(id -g)" -v "$PWD:/data" \
    ghcr.io/hank-ai/hankpdf:latest \
    /data/in.pdf -o /data/out.pdf

# macOS (Docker Desktop handles uid mapping automatically)
docker run --rm -v "$PWD:/data" ghcr.io/hank-ai/hankpdf:latest \
    /data/in.pdf -o /data/out.pdf

# Windows PowerShell (Docker Desktop handles uid mapping automatically)
docker run --rm -v "${PWD}:/data" ghcr.io/hank-ai/hankpdf:latest `
    /data/in.pdf -o /data/out.pdf
```

The container mounts your current directory at `/data` and runs
`hankpdf` against it. All CLI flags from the native install work
identically.

### About `-u "$(id -u):$(id -g)"` (Linux only)

The image runs as the non-root `hankpdf` user (uid 1000) for safety. On
Linux, bind-mounted host directories preserve their host ownership — if
your host uid isn't 1000, the container can't write to `/data`. Passing
`-u "$(id -u):$(id -g)"` runs the container as *your* uid so the output
lands with the right ownership and writes succeed.

Docker Desktop on macOS and Windows maps host uids transparently through
osxfs/virtiofs/WSL, so `-u` is unnecessary there.

## Tags

**Mutability matters — pick the right tag for your use case:**

| Tag | Mutable? | Points at | Use for |
|---|---|---|---|
| `:latest` | **MUTABLE** | newest `main` merge | local dev, quick tries |
| `:main` | **MUTABLE** | newest `main` merge | local dev, CI of downstream projects |
| `:vX.Y.Z` | **IMMUTABLE** | that exact release | production, signed batch jobs |
| `:vX.Y` | **MUTABLE** | newest patch within the minor | acceptable for production if you're okay with auto-patching |
| `:sha-<short>` | **IMMUTABLE** | that exact commit | production, reproducible research, bisecting |
| `@sha256:<digest>` | **IMMUTABLE** | those exact bytes | highest assurance — even a mutable-tag flip can't change what you pull |

**MUTABLE tags can be repointed at different bytes over time.** A
production batch job pinned to `:latest` will silently pick up a new
image next time Docker pulls, which may behave differently from the
one you tested. Pin to an IMMUTABLE tag (or a digest) for any run you
need to reproduce later.

See [SECURITY.md](../SECURITY.md#release-integrity) for the cosign +
SLSA verification recipes that confirm the digest you pulled really
did come from the build workflow you expected.

## Verifying the image

Every pushed image is signed with cosign (keyless, via GitHub's OIDC issuer)
and carries a SLSA v1 build-provenance attestation. Verify before running
in production:

```bash
# Requires cosign >= 2.0 (https://docs.sigstore.dev/cosign/installation/).
cosign verify ghcr.io/hank-ai/hankpdf:latest \
    --certificate-identity-regexp 'https://github\.com/hank-ai/(hankpdf|pdf-smasher)/\.github/workflows/docker\.yml@refs/(heads|tags)/.+' \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

Verify the SLSA provenance attestation:

```bash
gh attestation verify oci://ghcr.io/hank-ai/hankpdf:latest \
    --owner hank-ai
```

Inspect the SBOM (ships as an attestation on the image manifest):

```bash
docker buildx imagetools inspect ghcr.io/hank-ai/hankpdf:latest \
    --format "{{ json .SBOM }}"
```

## Build locally

```bash
docker build -f docker/Dockerfile -t hankpdf:dev .
docker run --rm hankpdf:dev --version
```

Multi-arch local builds (requires buildx):

```bash
docker buildx build -f docker/Dockerfile \
    --platform linux/amd64,linux/arm64 \
    -t hankpdf:dev .
```
