<#
.SYNOPSIS
    Install jbig2.exe (and bundled DLLs) for HankPDF on native Windows.

.DESCRIPTION
    Downloads a prebuilt jbig2-windows-x64.zip from the hankpdf GitHub
    Releases, extracts it to a local directory, registers that directory
    on the current user's PATH (HKCU, no admin required), and verifies
    the binary runs.

    The bundle is built reproducibly from agl/jbig2enc at a pinned commit
    by the windows-jbig2enc GitHub Actions workflow in this repo. No
    precompiled upstream binary exists - this is why the script pulls
    from our Releases instead of the upstream project.

.PARAMETER Destination
    Directory to install jbig2.exe and its DLLs into. Defaults to
    $env:LOCALAPPDATA\hankpdf\bin. Created if it does not exist.

.PARAMETER Version
    Release tag to install, e.g. "jbig2-windows-v0.1.0". Defaults to
    "latest" which resolves via the GitHub API.

.EXAMPLE
    # One-liner install from a TAGGED release (recommended):
    # main is a mutable branch — pin to a specific release tag.
    irm https://github.com/hank-ai/hankpdf/releases/download/jbig2-windows-v0.1.0/install_jbig2_windows.ps1 | iex

.EXAMPLE
    # Offline / manual install:
    .\install_jbig2_windows.ps1 -Destination C:\Tools\hankpdf\bin

.EXAMPLE
    # Pin to a specific release:
    .\install_jbig2_windows.ps1 -Version jbig2-windows-v0.1.0

.NOTES
    - Requires PowerShell 5.1+ or PowerShell 7+.
    - Does not require administrator - writes under %LOCALAPPDATA% and
      only modifies the user PATH, never the machine PATH.
    - Open a new terminal after install so the updated PATH is picked up.
#>

[CmdletBinding()]
param(
    [string]$Destination = (Join-Path $env:LOCALAPPDATA 'hankpdf\bin'),
    [string]$Version = 'latest'
)

$ErrorActionPreference = 'Stop'

# --- TLS / network hardening (E3) ------------------------------------
#
# PS 5.1's default SecurityProtocol is SSL3 + TLS 1.0, both of which
# GitHub has disabled. Force TLS 1.2, and also TLS 1.3 where the .NET
# framework on the box supports it (PS 7+ / .NET 4.8+). If we don't
# do this, `Invoke-WebRequest` fails with an opaque "underlying
# connection was closed" that users read as "the internet is broken".
[Net.ServicePointManager]::SecurityProtocol =
    [Net.ServicePointManager]::SecurityProtocol -bor
    [Net.SecurityProtocolType]::Tls12
if ([Net.SecurityProtocolType]::Tls13 -as [Net.SecurityProtocolType]) {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor
        [Net.SecurityProtocolType]::Tls13
}

# --- constants --------------------------------------------------------

$Repo      = 'hank-ai/hankpdf'
$AssetName = 'jbig2-windows-x64.zip'
$ApiBase   = 'https://api.github.com'
$UserAgent = 'hankpdf-install-jbig2/1.0 (+https://github.com/hank-ai/hankpdf)'
$HttpTimeoutSec = 120
$HttpMaxRetries = 3

# --- helpers ----------------------------------------------------------

function Write-Step {
    param([string]$Message)
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "    $Message" -ForegroundColor Green
}

function Write-Warn2 {
    param([string]$Message)
    Write-Warning $Message
}

function Invoke-WithRetry {
    <#
    .SYNOPSIS
        Run a script block with exponential-backoff retry on transient
        network failures (E3).
    #>
    param(
        [scriptblock]$ScriptBlock,
        [int]$MaxAttempts = $HttpMaxRetries,
        [string]$OpName = 'HTTP request'
    )
    $attempt = 0
    while ($true) {
        $attempt++
        try {
            return & $ScriptBlock
        } catch [System.Net.WebException] {
            $status = $null
            if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
            # 4xx (except 408/429) aren't retryable — no amount of
            # waiting fixes a missing resource.
            if ($status -ge 400 -and $status -lt 500 -and $status -ne 408 -and $status -ne 429) {
                throw
            }
            if ($attempt -ge $MaxAttempts) {
                throw
            }
            $sleep = [math]::Pow(2, $attempt)  # 2s, 4s, 8s
            Write-Host "    ${OpName} attempt ${attempt}/${MaxAttempts} failed: $($_.Exception.Message); retrying in ${sleep}s..."
            Start-Sleep -Seconds $sleep
        }
    }
}

function Invoke-GitHubApi {
    param([string]$Url)
    $headers = @{
        'Accept'               = 'application/vnd.github+json'
        'User-Agent'           = $UserAgent
        'X-GitHub-Api-Version' = '2022-11-28'
    }
    # GitHub caps unauthenticated requests at 60/hr/IP; bubble up a
    # clear message instead of dumping the raw exception.
    try {
        return Invoke-WithRetry -OpName "GitHub API" -ScriptBlock {
            Invoke-RestMethod -Uri $Url -Headers $headers -UseBasicParsing -TimeoutSec $HttpTimeoutSec
        }
    }
    catch {
        $resp = $_.Exception.Response
        $status = $null
        if ($resp) { $status = [int]$resp.StatusCode }
        if ($status -eq 403) {
            throw "GitHub API rate-limited (HTTP 403). Try again later, or pass -Version <tag> with an explicit release tag to avoid the API call."
        }
        if ($status -eq 404) {
            throw "GitHub API returned 404 for $Url. If this is the first run of the build workflow, no releases have been published yet - trigger the 'Windows jbig2.exe build' workflow via workflow_dispatch, then create a release tagged 'jbig2-windows-v0.1.0' with the built artifact."
        }
        throw "GitHub API request failed: $($_.Exception.Message)"
    }
}

function Resolve-ReleaseAsset {
    param(
        [string]$Repo,
        [string]$Version,
        [string]$AssetName
    )

    if ($Version -eq 'latest') {
        $url = "$ApiBase/repos/$Repo/releases/latest"
    }
    else {
        $url = "$ApiBase/repos/$Repo/releases/tags/$Version"
    }

    Write-Step "Resolving release $Version from $Repo"
    $release = Invoke-GitHubApi -Url $url

    if (-not $release -or -not $release.assets) {
        throw "Release $Version has no assets. No Windows jbig2 binary has been published yet - run the windows-jbig2enc workflow first."
    }

    $asset = $release.assets | Where-Object { $_.name -eq $AssetName } | Select-Object -First 1
    if (-not $asset) {
        $names = ($release.assets | ForEach-Object { $_.name }) -join ', '
        throw "Release $($release.tag_name) does not contain an asset named '$AssetName'. Assets present: $names"
    }

    Write-Ok "Found $AssetName in release $($release.tag_name)"

    # Also locate the SHA-256 sidecar. This is required — without it we
    # have no way to verify the zip we downloaded is the one CI produced.
    # If the release is missing the sidecar, refuse the install rather
    # than silently skip the check (degrades-open is worse than degrades-
    # closed for supply-chain).
    $sidecarName = "$AssetName.sha256"
    $sidecar = $release.assets | Where-Object { $_.name -eq $sidecarName } | Select-Object -First 1
    if (-not $sidecar) {
        throw "Release $($release.tag_name) is missing '$sidecarName'. Refusing to install without a SHA-256 sidecar (supply-chain hygiene). Re-run the Windows release workflow with A7 or newer, or pass -Version to pin to a release that has one."
    }

    return [PSCustomObject]@{
        Tag             = $release.tag_name
        DownloadUrl     = $asset.browser_download_url
        Size            = $asset.size
        SidecarUrl      = $sidecar.browser_download_url
    }
}

function Get-ExpectedSha256 {
    param([string]$SidecarUrl)

    # Sidecar is GNU sha256sum format: "<64 hex chars>  <filename>". We
    # only trust the hex; the filename is informational. Strip whitespace
    # and lowercase for case-insensitive comparison against Get-FileHash.
    $ua = $UserAgent
    $timeoutSec = $HttpTimeoutSec
    $content = (Invoke-WithRetry -OpName "sidecar fetch" -ScriptBlock {
        Invoke-WebRequest -Uri $SidecarUrl `
            -UseBasicParsing `
            -Headers @{ 'User-Agent' = $ua } `
            -TimeoutSec $timeoutSec
    }).Content
    $firstToken = ($content -split '\s+', 2)[0].Trim().ToLowerInvariant()
    if ($firstToken -notmatch '^[a-f0-9]{64}$') {
        throw "Sidecar did not contain a 64-hex-char SHA-256 digest. Got: $firstToken"
    }
    return $firstToken
}

function Add-ToUserPath {
    param([string]$Directory)

    $current = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not $current) { $current = '' }

    # Case-insensitive, trailing-slash-tolerant duplicate check.
    $needle = $Directory.TrimEnd('\').ToLowerInvariant()
    $parts = $current -split ';' | Where-Object { $_ -ne '' }
    $already = $parts | Where-Object { $_.TrimEnd('\').ToLowerInvariant() -eq $needle }

    if ($already) {
        Write-Ok "User PATH already contains $Directory"
        return $false
    }

    $new = if ($current) { "$current;$Directory" } else { $Directory }
    [Environment]::SetEnvironmentVariable('Path', $new, 'User')
    Write-Ok "Added $Directory to user PATH"
    return $true
}

# --- main -------------------------------------------------------------

try {
    Write-Step "HankPDF: installing jbig2.exe on native Windows"
    Write-Host "    Destination: $Destination"
    Write-Host "    Version:     $Version"
    Write-Host ""

    # 1. Resolve the release and pick the asset.
    $info = Resolve-ReleaseAsset -Repo $Repo -Version $Version -AssetName $AssetName

    # 2. Download to a temp file.
    $tempDir = Join-Path $env:TEMP ("hankpdf-jbig2-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
    $zipPath = Join-Path $tempDir $AssetName

    try {
        Write-Step "Downloading $AssetName"
        # Invoke-WebRequest honors system proxy; UseBasicParsing keeps
        # it working on Server Core / constrained PowerShell installs.
        # Wrapped in retry (E3): transient 500/503/timeouts retry up
        # to 3 times with exponential backoff; 4xx fails fast.
        $ProgressPreference = 'SilentlyContinue'
        $downloadUrl = $info.DownloadUrl
        $ua = $UserAgent
        $timeoutSec = $HttpTimeoutSec
        Invoke-WithRetry -OpName "download" -ScriptBlock {
            Invoke-WebRequest -Uri $downloadUrl `
                -OutFile $zipPath `
                -UseBasicParsing `
                -Headers @{ 'User-Agent' = $ua } `
                -TimeoutSec $timeoutSec
        }

        $fi = Get-Item $zipPath -ErrorAction Stop
        if ($fi.Length -le 0) {
            throw "Downloaded file is empty: $zipPath"
        }
        Write-Ok "Downloaded $($fi.Length) bytes"

        # 2a. Verify SHA-256 against the sidecar published alongside the
        #     zip. Any mismatch means either (a) the download was
        #     corrupted/MITM'd, or (b) the release was tampered with
        #     post-publish. Fail hard — do NOT install. The user's
        #     existing install (if any) is untouched.
        Write-Step "Verifying SHA-256 against release sidecar"
        $expected = Get-ExpectedSha256 -SidecarUrl $info.SidecarUrl
        $actual = (Get-FileHash -Path $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected) {
            throw "[E-SHA256-MISMATCH] SHA-256 mismatch on $AssetName. Expected $expected, got $actual. Refusing to install a download that doesn't match the release sidecar; your existing install (if any) is unchanged."
        }
        Write-Ok "SHA-256 verified: $actual"

        # 3. Atomic install via rename-swap (E4).
        #
        # Previously we extracted to staging and copy-item'd each file
        # into $Destination one by one — a crash mid-copy left the user
        # with a half-populated install directory, corrupt from the
        # perspective of any tool that had already picked the stale
        # binary up via PATH. Per-file Copy-Item is also O(files)
        # expensive when the bundle has 15+ DLLs.
        #
        # New flow:
        #   1. Build the full new install in "$Destination.new"
        #   2. Move any existing "$Destination" to "$Destination.old"
        #   3. Rename "$Destination.new" -> "$Destination"
        #   4. rm -rf "$Destination.old"
        # Between steps 2 and 3 there's a microsecond window where
        # neither path exists, but no concurrent consumer can hit a
        # half-populated state. On Windows, Move-Item within the same
        # filesystem is atomic (NTFS rename).
        Write-Step "Extracting to $Destination (atomic rename-swap)"
        $destNew = "$Destination.new"
        $destOld = "$Destination.old"
        # Clean up lingering "$.new" or "$.old" from a prior failed run.
        if (Test-Path $destNew) { Remove-Item -Recurse -Force $destNew }
        if (Test-Path $destOld) { Remove-Item -Recurse -Force $destOld }
        New-Item -ItemType Directory -Path $destNew -Force | Out-Null

        $stagingDir = Join-Path $tempDir 'extracted'
        New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null

        # Use System.IO.Compression instead of Expand-Archive so we can
        # defend against zip-slip (../ escape) — Expand-Archive on PS 5.1
        # silently follows path-traversal sequences in zip entries and
        # writes outside $stagingDir. Each entry's destination is
        # resolved and compared to the staging root; any entry outside
        # that tree is refused.
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $stagingFull = [System.IO.Path]::GetFullPath($stagingDir).TrimEnd('\')
        $archive = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
        try {
            foreach ($entry in $archive.Entries) {
                $target = [System.IO.Path]::GetFullPath(
                    [System.IO.Path]::Combine($stagingDir, $entry.FullName))
                if (-not $target.StartsWith($stagingFull + [System.IO.Path]::DirectorySeparatorChar) `
                    -and $target -ne $stagingFull) {
                    throw "[E-EXTRACT] Zip-slip attempt detected: entry '$($entry.FullName)' resolves outside the staging dir; refusing to extract."
                }
                $parent = Split-Path -Parent $target
                if ($parent) {
                    New-Item -ItemType Directory -Path $parent -Force | Out-Null
                }
                if ($entry.FullName.EndsWith('/')) {
                    # Directory entry; already created above.
                    continue
                }
                [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $true)
            }
        } finally {
            $archive.Dispose()
        }

        # Zip contains a top-level 'jbig2-windows-x64/' directory;
        # flatten into $destNew so jbig2.exe lives at the root.
        $inner = Join-Path $stagingDir 'jbig2-windows-x64'
        $sourceRoot = if (Test-Path $inner) { $inner } else { $stagingDir }

        Get-ChildItem -Path $sourceRoot -Force | ForEach-Object {
            $target = Join-Path $destNew $_.Name
            Copy-Item -Path $_.FullName -Destination $target -Recurse -Force
        }

        $newExe = Join-Path $destNew 'jbig2.exe'
        if (-not (Test-Path $newExe)) {
            throw "Extraction completed but $newExe was not created. Zip contents may be malformed."
        }

        # Atomic swap.
        if (Test-Path $Destination) {
            Move-Item -Path $Destination -Destination $destOld -Force
        }
        try {
            Move-Item -Path $destNew -Destination $Destination -Force
        } catch {
            # Rollback: restore the old install if the new-rename failed.
            if (Test-Path $destOld) {
                Move-Item -Path $destOld -Destination $Destination -Force
            }
            throw
        }
        if (Test-Path $destOld) {
            Remove-Item -Recurse -Force $destOld -ErrorAction SilentlyContinue
        }

        $exe = Join-Path $Destination 'jbig2.exe'
        if (-not (Test-Path $exe)) {
            throw "Atomic swap completed but $exe is missing. Filesystem consistency issue."
        }
        Write-Ok "Installed jbig2.exe and bundled DLLs (atomic swap)"
    }
    finally {
        if (Test-Path $tempDir) {
            Remove-Item -Path $tempDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    # 4. Register on user PATH.
    Write-Step "Registering on user PATH"
    $added = Add-ToUserPath -Directory $Destination

    # 5. Verify the binary runs. jbig2enc prints usage and exits non-zero
    #    when given no args - any output is proof the DLLs loaded.
    Write-Step "Verifying jbig2.exe"
    $verifyOutput = & $exe 2>&1
    if ([string]::IsNullOrWhiteSpace(($verifyOutput | Out-String))) {
        throw "jbig2.exe produced no output when invoked. DLL loading likely failed; check that every .dll in $Destination is present and unblocked (Unblock-File)."
    }
    Write-Ok "jbig2.exe runs and emits usage text - install verified"

    # 6. Summary.
    Write-Host ""
    Write-Host "HankPDF jbig2 install complete." -ForegroundColor Green
    Write-Host "    Installed to: $Destination"
    Write-Host "    Release:      $($info.Tag)"
    if ($added) {
        Write-Host ""
        Write-Host "PATH was updated for the current user. Open a NEW terminal"
        Write-Host "(or log out and back in) so Python / HankPDF can discover"
        Write-Host "jbig2.exe. To verify from a fresh shell:"
        Write-Host ""
        Write-Host "    jbig2.exe      # should print usage" -ForegroundColor Yellow
        Write-Host "    hankpdf --doctor" -ForegroundColor Yellow
    }
    else {
        Write-Host "    PATH already contained $Destination; no change."
    }
}
catch [System.Net.WebException] {
    # Network / DNS / TLS / proxy failures. HTTP 403/404 from the
    # GitHub API are translated to more specific exceptions inside
    # Invoke-GitHubApi so we don't land here for those.
    Write-Host ""
    Write-Host "[E-NETWORK] Network request failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Hint: check proxy/VPN, corporate firewall, or re-run in a minute."
    exit 2
}
catch [System.IO.InvalidDataException] {
    # Expand-Archive throws this for malformed zips.
    Write-Host ""
    Write-Host "[E-EXTRACT] Archive extraction failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Hint: the download may have been truncated; re-run to retry."
    exit 3
}
catch [System.UnauthorizedAccessException] {
    # Target dir / PATH key not writable by the current user.
    Write-Host ""
    Write-Host "[E-PERMISSIONS] Permission denied: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Hint: pick a different -Destination or run from a user with write access."
    exit 4
}
catch {
    # Everything else — SHA-256 mismatch (our own throw), missing
    # sidecar, etc. We explicitly label the common cases so on-call
    # logs are greppable by [E-*] code.
    $msg = $_.Exception.Message
    Write-Host ""
    if ($msg -match '\[E-SHA256-MISMATCH\]') {
        Write-Host "[E-SHA256-MISMATCH] $msg" -ForegroundColor Red
        exit 5
    }
    Write-Host "[E-UNKNOWN] $msg" -ForegroundColor Red
    Write-Host ""
    Write-Host "Fallback: HankPDF still works without jbig2.exe - the MRC"
    Write-Host "pipeline falls back to CCITT G4, which produces outputs"
    Write-Host "roughly 10-20% larger but otherwise functionally identical."
    exit 1
}
