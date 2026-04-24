# scripts/

Utility scripts used during development and installation.

## `install_jbig2_windows.ps1`

PowerShell installer that fetches a prebuilt `jbig2.exe` (with all
required DLLs) from the hankpdf GitHub Releases, extracts it to
`%LOCALAPPDATA%\hankpdf\bin`, and adds that directory to the current
user's PATH. Runs without administrator.

Invoke directly from the repo:

```powershell
irm https://raw.githubusercontent.com/hank-ai/hankpdf/main/scripts/install_jbig2_windows.ps1 | iex
```

Or offline, with custom install location:

```powershell
.\install_jbig2_windows.ps1 -Destination C:\Tools\hankpdf\bin
```

Or pin to a specific release tag:

```powershell
.\install_jbig2_windows.ps1 -Version jbig2-windows-v0.1.0
```

See the script's embedded `Get-Help` documentation for full parameter
and example coverage.

## Other scripts

- `fetch_corpus.py` — fetch test corpora used by compression benchmarks.
- `generate_corpus` — generate synthetic test PDFs.
- `make_smoke_fixture.py` — produce the minimal smoke-test PDF used by
  Docker CI and local quickstart.
- `measure_ratios.py` — measure compression ratios across a corpus.
- `spike_mrc.py` — experimental MRC layer spike utility.

## Publishing the first Windows jbig2 release

The PowerShell installer resolves the download URL via the GitHub
Releases API. Until a release exists under the `jbig2-windows-v*` tag
pattern, `irm | iex` will fail with a helpful message. To publish the
first release after this branch merges to `main`:

1. Trigger `.github/workflows/windows-jbig2enc.yml` via
   **Actions → Windows jbig2.exe build → Run workflow** (uses the
   workflow's `workflow_dispatch` trigger).
2. Download the `jbig2-windows-x64` artifact from the workflow run
   (a zip containing `jbig2.exe` plus its runtime DLLs, built from
   [agl/jbig2enc](https://github.com/agl/jbig2enc) at the pinned commit
   in the workflow file — must match `docker/Dockerfile`'s
   `JBIG2ENC_COMMIT`).
3. Create a GitHub Release tagged `jbig2-windows-v0.1.0` and upload
   the zip as a release asset. (The tag name prefix is what makes
   subsequent builds auto-publish on push — see the workflow's
   `push: tags` trigger.)

Subsequent releases can skip the manual upload: push a
`jbig2-windows-v*` tag to `main` and the workflow creates the release
and attaches the zip automatically via `softprops/action-gh-release`.
