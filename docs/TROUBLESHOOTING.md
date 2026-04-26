# Troubleshooting

On-call playbook for diagnosing a HankPDF run in production. Everything
here works without HankPDF ever logging a plaintext filename — the
correlation-id flow in §2 is the bridge between redacted stderr and the
user's own "which file was that" table.

## 1. Gather the identity of the run

`hankpdf --version` prints:

```
hankpdf <version> (git <sha-7>, built <date>, image <digest-7>, python <ver>)
```

Docker images also carry `/etc/hankpdf/build-info.json`:

```bash
docker run --rm ghcr.io/hank-ai/hankpdf:latest \
    cat /etc/hankpdf/build-info.json
```

Ship both in any bug report.

## 2. The correlation-ID recovery workflow

Every `hankpdf` invocation generates a UUID4 correlation id at startup.
Two places carry it:

- **stderr lines**: every `[hankpdf] warning …` / `[hankpdf] error …` /
  prefix line includes ` corr=<first-8-chars>`.
- **`{output}_correlation.json`**: sidecar written next to the output PDF
  when output is a file. Contains the full UUID plus the input SHA-256
  and size.

Because the sidecar never records the input filename (THREAT_MODEL.md
§5), the on-call path is:

1. User reports "the run of `patient-NNNN-visit-Q.pdf` produced garbage
   output". They do NOT send us the file. We do NOT record the filename.
2. Ask the user for:
   - The correlation id on the stderr line (short form: `corr=a1b2c3d4`),
     OR
   - The contents of `{output_stem}_correlation.json` next to the bad
     output.
3. Whichever they give, we derive **`input_sha256`**.
4. User runs `sha256sum patient-NNNN-visit-Q.pdf` on their side and
   confirms the hash matches. Now both sides know we're talking about
   the same byte sequence without anyone ever having exchanged the
   filename.
5. If the hash matches an input in our synthetic-corpus regression tests,
   we can reproduce locally. If not, user can optionally ship the
   redacted file (per their own governance).

### Example: map a stderr line back to a report

```
$ grep 'corr=a1b2c3d4' batch.log
[hankpdf] corr=a1b2c3d4 xyz89abc-visit.pdf: warning [W-VERIFIER-SKIPPED]: …
```

```bash
$ cat batch_output_correlation.json | jq .entries[]
{
  "correlation_id": "a1b2c3d4...32-chars",
  "input_sha256": "sha256:deadbeefcafebabefeeddeadbeefcafebabefeed...",
  "input_size": 12345678,
  "exit_code": 0,
  "output_path": "batch_output.pdf"
}
```

The `correlation_id` in the sidecar starts with the same 8 chars that
appear on the stderr line; the full UUID disambiguates across a batch
run where multiple invocations could (astronomically rarely) collide in
the first 8 hex chars.

## 3. Common refusal paths

| Exit code | Stderr code | Cause | First-line fix |
|---|---|---|---|
| 10 | `E-INPUT-ENCRYPTED` | PDF has a password and none was supplied | Pass `--password-file` or `HANKPDF_PASSWORD` |
| 11 | `E-INPUT-SIGNED` | PDF has a signature | Add `--allow-signed-invalidation` if the signature is safe to break |
| 12 | `E-INPUT-OVERSIZE` | Input exceeds `--max-input-mb` | Raise the cap, or pre-split the input |
| 13 | `E-INPUT-CORRUPT` | qpdf / pikepdf can't parse the PDF | Run `qpdf --check input.pdf` to triangulate |
| 14 | `E-INPUT-MALICIOUS` | JBIG2 reuse / JS / embedded file bypass | Audit with `qpdf --json` before retrying |
| 15 | `E-INPUT-CERTIFIED` | Certifying signature would be invalidated | Needs `--allow-certified-invalidation` AND explicit user buy-in |
| 16 | `E-INPUT-DECOMPRESSION-BOMB` | A page raster would exceed `PIL.Image.MAX_IMAGE_PIXELS` | Reduce `--image-dpi` (image-export) or inspect the input |
| 20 | `E-VERIFIER-FAIL` | Content-preservation gate tripped | Re-run without `--verify`, or with `--accept-drift` for a warning-only outcome |
| 30 | `E-ENGINE-ERROR` / `E-TIMEOUT-*` | Engine crashed / timed out | Check stderr for the stack trace; bump `--per-page-timeout-seconds` if genuinely slow input |

## 4. Verifier-skipped warnings are intentional

`W-VERIFIER-SKIPPED` fires on every run that didn't pass `--verify`.
That's the default since v0.0.x (verifier adds ~2–5 s/page and is noisy
on scans with antialiased source text). If you need post-hoc
content-preservation proof for a specific run, re-run with `--verify`.

## 5. Docker: "`/data` is not writable"

The image runs as uid 1000. On Linux, bind-mounted host directories
preserve host ownership — if your uid isn't 1000, the container can't
write to `/data`. Pass `-u $(id -u):$(id -g)`:

```bash
docker run --rm -u "$(id -u):$(id -g)" -v "$PWD:/data" \
    ghcr.io/hank-ai/hankpdf:latest /data/in.pdf -o /data/out.pdf
```

Docker Desktop on macOS / Windows handles uid mapping automatically.

## 6. Windows: `jbig2.exe` not on PATH

Run `hankpdf --doctor`. If `jbig2` shows `NOT FOUND`, either install
via the release-pinned PowerShell script:

```powershell
$tag = "jbig2-windows-v0.1.0"
irm "https://github.com/hank-ai/hankpdf/releases/download/$tag/install_jbig2_windows.ps1" | iex
```

…or HankPDF will fall back to CCITT G4 for the text layer (outputs
10–20% larger but otherwise identical). The fallback is silent except
for a warning in the run's `CompressReport.warnings`.
