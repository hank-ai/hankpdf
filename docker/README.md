# Docker

The Dockerfile in this directory is the source for the published image at
`ghcr.io/ourorg/pdf-smasher`. Phase 0 ships a **skeleton only**; Phase 6
finalizes it (image size optimization, pinned native-dep versions, seccomp
profile).

## Build locally

```bash
docker build -f docker/Dockerfile -t hankpdf:dev .
```

## Run locally

```bash
docker run --rm hankpdf:dev --version
```

## Production usage (end users)

See `docs/INSTALL.md`.
