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

- `:latest` — most recent commit on `main`
- `:vX.Y.Z` — released versions
- `:sha-<short>` — specific commit

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
