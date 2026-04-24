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

Option A — Chocolatey + winget + our prebuilt jbig2:

```powershell
winget install Python.Python.3.14
choco install tesseract qpdf -y

# Prebuilt jbig2.exe (no apt/brew on Windows, so we publish our own).
# Installs to %LOCALAPPDATA%\hankpdf\bin and adds to user PATH.
# No administrator required. Open a new terminal after it runs.
irm https://raw.githubusercontent.com/hank-ai/hankpdf/main/scripts/install_jbig2_windows.ps1 | iex

pip install pdf-smasher
hankpdf --doctor
```

If the jbig2 installer fails (no release published yet, API rate-limit,
corporate proxy blocking github.com), HankPDF still works — the MRC
pipeline falls back to CCITT G4 for the text layer. Outputs are
typically 10-20% larger than with jbig2enc, but every other feature
works identically and all tests pass. You can re-run the installer
later, or install `jbig2.exe` manually by downloading
`jbig2-windows-x64.zip` from the
[hankpdf Releases](https://github.com/hank-ai/hankpdf/releases) page
and placing the extracted directory on your PATH.

Option B — **use Docker Desktop instead**. Simpler on Windows.

## Docker (every OS)

Zero host setup. All native deps baked in.

```bash
docker pull ghcr.io/hank-ai/hankpdf:latest

docker run --rm \
  -v "$PWD:/data" \
  --user "$(id -u):$(id -g)" \
  ghcr.io/hank-ai/hankpdf:latest \
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
  ghcr.io/hank-ai/hankpdf:latest \
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
docker pull ghcr.io/hank-ai/hankpdf:latest
```

No auto-update mechanism. You control when to upgrade.
