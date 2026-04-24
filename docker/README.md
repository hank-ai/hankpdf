# Docker

HankPDF is published as a multi-arch Docker image on GHCR. All native
dependencies (Tesseract, qpdf, jbig2enc) are baked in — zero host setup.

## Pull

```bash
docker pull ghcr.io/hank-ai/hankpdf:latest
```

## Run

```bash
# Linux / macOS
docker run --rm -v "$PWD:/data" ghcr.io/hank-ai/hankpdf:latest \
    /data/in.pdf -o /data/out.pdf

# Windows PowerShell
docker run --rm -v "${PWD}:/data" ghcr.io/hank-ai/hankpdf:latest `
    /data/in.pdf -o /data/out.pdf
```

The container mounts your current directory at `/data` and runs
`hankpdf` against it. All CLI flags from the native install work
identically.

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
