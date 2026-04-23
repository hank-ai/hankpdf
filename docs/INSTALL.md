# Install

HankPDF ships as a Python package. It needs a few native dependencies installed on the host: Tesseract 5, qpdf, and jbig2enc. One-line install on every major OS.

> **Shortcut**: if you don't want to manage host deps, just use the Docker image — everything is baked in. See the Docker section below.

## macOS

```bash
brew install tesseract qpdf
# jbig2enc: build from source (we vendor a pinned commit)
brew install autoconf automake libtool leptonica
git clone --depth=1 https://github.com/agl/jbig2enc.git /tmp/jbig2enc
cd /tmp/jbig2enc && ./autogen.sh && ./configure && make && sudo make install

pip install pdf-smasher
hankpdf --doctor
```

## Debian / Ubuntu

```bash
sudo apt update
sudo apt install -y tesseract-ocr libtesseract-dev qpdf jbig2enc-tools

pip install pdf-smasher
hankpdf --doctor
```

## Fedora / RHEL

```bash
sudo dnf install -y tesseract qpdf leptonica-devel
# jbig2enc: build from source (most RPM distros don't package it)
sudo dnf install -y autoconf automake libtool gcc-c++ make
git clone --depth=1 https://github.com/agl/jbig2enc.git /tmp/jbig2enc
cd /tmp/jbig2enc && ./autogen.sh && ./configure && make && sudo make install

pip install pdf-smasher
hankpdf --doctor
```

## Windows

Option A — Chocolatey / Scoop:

```powershell
choco install tesseract qpdf
# jbig2enc on Windows: download our vendored prebuilt binary from the GitHub release
# and place jbig2.exe on PATH.

pip install pdf-smasher
hankpdf --doctor
```

Option B — **use Docker Desktop instead**. Simpler on Windows.

## Docker (every OS)

Zero host setup. All native deps baked in.

```bash
docker pull ghcr.io/ourorg/pdf-smasher:latest

docker run --rm \
  -v "$PWD:/data" \
  --user "$(id -u):$(id -g)" \
  ghcr.io/ourorg/pdf-smasher:latest \
  /data/input.pdf -o /data/output.pdf
```

For stricter isolation (no network, read-only rootfs):

```bash
docker run --rm \
  --network none \
  --read-only \
  --tmpfs /tmp:rw,size=4g \
  -v "$PWD:/data" \
  --user "$(id -u):$(id -g)" \
  ghcr.io/ourorg/pdf-smasher:latest \
  /data/input.pdf -o /data/output.pdf
```

## Verify

```bash
hankpdf --doctor
```

Should print Python version, pdf-smasher version, engine version, and the version + path of every native dep. Exits 0 if the environment passes all floor checks (qpdf ≥ 11.6.3, OpenJPEG ≥ 2.5.4, Pillow `MAX_IMAGE_PIXELS` set, etc. — see `docs/ARCHITECTURE.md` §3 and `docs/ROADMAP.md` T0.9).

If anything is missing or out of date, `--doctor` exits 41 with a specific remediation message per missing item.

## Upgrading

```bash
pip install -U pdf-smasher
# or for Docker:
docker pull ghcr.io/ourorg/pdf-smasher:latest
```

No auto-update mechanism. You control when to upgrade.
