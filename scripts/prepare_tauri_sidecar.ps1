param(
  [switch]$SkipInstall,
  [switch]$IncludeCuda,
  [string]$TargetTriple
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Assert-NativeSuccess {
  param([Parameter(Mandatory = $true)][string]$Step)
  if ($LASTEXITCODE -ne 0) {
    throw "$Step failed with exit code $LASTEXITCODE"
  }
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  throw "python not found"
}
if (-not (Get-Command rustc -ErrorAction SilentlyContinue)) {
  throw "rustc not found. Install Rust first so the Tauri target triple can be resolved."
}

if (-not $TargetTriple) {
  $rustInfo = & rustc -vV
  Assert-NativeSuccess "rustc -vV"
  $hostLine = $rustInfo | Select-String '^host:' | Select-Object -First 1
  if (-not $hostLine) { throw "Could not read Rust host target triple" }
  $TargetTriple = ($hostLine.ToString() -replace '^host:\s*', '').Trim()
}

$BuildRoot = Join-Path $Root ".build\sidecar"
$VenvRoot = Join-Path $BuildRoot "venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
$DistRoot = Join-Path $BuildRoot "dist"
$WorkRoot = Join-Path $BuildRoot "work"
$SpecRoot = Join-Path $BuildRoot "spec"
$BinaryDir = Join-Path $Root "desktop\src-tauri\binaries"
New-Item -ItemType Directory -Force $BuildRoot, $DistRoot, $WorkRoot, $SpecRoot, $BinaryDir | Out-Null

# Build in an isolated environment. Using the developer's global Python caused
# PyInstaller to discover unrelated packages (Torch/Paddle/ModelScope/etc.) and
# produced a multi-gigabyte sidecar that NSIS cannot memory-map reliably.
if (-not (Test-Path $VenvPython)) {
  if ($SkipInstall) {
    throw "-SkipInstall was requested, but the isolated sidecar environment does not exist: $VenvPython"
  }
  Write-Host "Creating isolated sidecar environment..." -ForegroundColor Cyan
  & python -m venv $VenvRoot
  Assert-NativeSuccess "sidecar virtual environment creation"
}

if (-not $SkipInstall) {
  Write-Host "Installing isolated sidecar dependencies..." -ForegroundColor Cyan
  & $VenvPython -m pip install --upgrade pip wheel "setuptools<81"
  Assert-NativeSuccess "sidecar build-tool installation"
  & $VenvPython -m pip install -r requirements\sidecar.txt -r requirements\build.txt
  Assert-NativeSuccess "sidecar dependency installation"
  if ($IncludeCuda) {
    Write-Warning "-IncludeCuda is ignored for the sidecar; CUDA now belongs to the transcription-cuda runtime component."
  }
}

$separator = [IO.Path]::PathSeparator
$templateYaml = (Join-Path $Root "src\application\notes\templates") + $separator + "src\application\notes\templates"
$templateMd = (Join-Path $Root "templates") + $separator + "templates"
$source = Join-Path $DistRoot "python-engine.exe"
$destination = Join-Path $BinaryDir "python-engine-$TargetTriple.exe"
# Never accept a stale sidecar from an earlier failed build.
Remove-Item -Force $source -ErrorAction SilentlyContinue
Remove-Item -Force $destination -ErrorAction SilentlyContinue

Write-Host "Building isolated Python sidecar for $TargetTriple..." -ForegroundColor Cyan
& $VenvPython -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --console `
  --name python-engine `
  --distpath $DistRoot `
  --workpath $WorkRoot `
  --specpath $SpecRoot `
  --paths $Root `
  --collect-submodules src `
  --exclude-module PySide6 `
  --exclude-module tkinter `
  --exclude-module torch `
  --exclude-module tensorflow `
  --exclude-module paddle `
  --exclude-module paddleocr `
  --exclude-module paddlex `
  --exclude-module modelscope `
  --exclude-module datasets `
  --exclude-module transformers `
  --exclude-module matplotlib `
  --exclude-module pytest `
  --exclude-module IPython `
  --add-data $templateYaml `
  --add-data $templateMd `
  src\engine.py
Assert-NativeSuccess "PyInstaller sidecar build"

if (-not (Test-Path $source)) {
  throw "PyInstaller exited successfully but did not create $source"
}

$sizeBytes = (Get-Item $source).Length
$sizeMiB = [math]::Round($sizeBytes / 1MB, 1)
Write-Host "Sidecar size: $sizeMiB MiB" -ForegroundColor Cyan

# NSIS/makensis has known mmap/2 GiB constraints. Leave headroom for the app,
# WebView bootstrap and installer metadata instead of failing late in bundling.
$maxBytes = 1700MB
if ($sizeBytes -gt $maxBytes) {
  throw "The isolated sidecar is still too large for the NSIS installer ($sizeMiB MiB; limit 1700 MiB). Check .build\sidecar\work\python-engine\warn-python-engine.txt for accidental heavy dependencies."
}

Copy-Item -Force $source $destination
if (-not (Test-Path $destination)) {
  throw "Could not stage Tauri sidecar at $destination"
}
$fingerprint = (& python "$PSScriptRoot\compute_sidecar_fingerprint.py").Trim()
Assert-NativeSuccess "sidecar source fingerprint"
Set-Content -Path "$destination.fingerprint" -Value $fingerprint -Encoding ascii
Write-Host "Sidecar ready: $destination" -ForegroundColor Green
