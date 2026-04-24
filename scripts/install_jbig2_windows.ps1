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
    # One-liner install from the repo (recommended):
    irm https://raw.githubusercontent.com/hank-ai/hankpdf/main/scripts/install_jbig2_windows.ps1 | iex

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

# --- constants --------------------------------------------------------

$Repo      = 'hank-ai/hankpdf'
$AssetName = 'jbig2-windows-x64.zip'
$ApiBase   = 'https://api.github.com'
$UserAgent = 'hankpdf-install-jbig2/1.0 (+https://github.com/hank-ai/hankpdf)'

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
        return Invoke-RestMethod -Uri $Url -Headers $headers -UseBasicParsing
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
    return [PSCustomObject]@{
        Tag         = $release.tag_name
        DownloadUrl = $asset.browser_download_url
        Size        = $asset.size
    }
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
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $info.DownloadUrl -OutFile $zipPath -UseBasicParsing -Headers @{ 'User-Agent' = $UserAgent }

        $fi = Get-Item $zipPath -ErrorAction Stop
        if ($fi.Length -le 0) {
            throw "Downloaded file is empty: $zipPath"
        }
        Write-Ok "Downloaded $($fi.Length) bytes"

        # 3. Ensure destination exists and extract.
        if (-not (Test-Path -Path $Destination)) {
            New-Item -ItemType Directory -Path $Destination -Force | Out-Null
        }

        Write-Step "Extracting to $Destination"
        $stagingDir = Join-Path $tempDir 'extracted'
        Expand-Archive -Path $zipPath -DestinationPath $stagingDir -Force

        # The zip contains a top-level 'jbig2-windows-x64/' directory;
        # flatten it into $Destination so jbig2.exe lives at the root.
        $inner = Join-Path $stagingDir 'jbig2-windows-x64'
        $sourceRoot = if (Test-Path $inner) { $inner } else { $stagingDir }

        Get-ChildItem -Path $sourceRoot -Force | ForEach-Object {
            $target = Join-Path $Destination $_.Name
            Copy-Item -Path $_.FullName -Destination $target -Recurse -Force
        }

        $exe = Join-Path $Destination 'jbig2.exe'
        if (-not (Test-Path $exe)) {
            throw "Extraction completed but $exe was not created. Zip contents may be malformed."
        }
        Write-Ok "Installed jbig2.exe and bundled DLLs"
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
catch {
    Write-Host ""
    Write-Host "FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
    Write-Host "Fallback: HankPDF still works without jbig2.exe - the MRC"
    Write-Host "pipeline falls back to CCITT G4, which produces outputs"
    Write-Host "roughly 10-20% larger but otherwise functionally identical."
    exit 1
}
