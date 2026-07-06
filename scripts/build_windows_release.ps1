param(
  [switch]$SkipSidecarInstall,
  [switch]$ReuseSidecar,
  [switch]$IncludeCuda
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

foreach ($command in @("node", "npm", "rustc", "cargo")) {
  if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
    throw "$command not found"
  }
}

if ($SkipSidecarInstall -or $ReuseSidecar -or $IncludeCuda) {
  Write-Warning "Sidecar/CUDA build switches are ignored. The Windows release now uses the Rust native engine and runtime components."
}

# Normalize a crates.io mirror mismatch seen on some Windows installations.
$CargoLock = Join-Path $Root "desktop\src-tauri\Cargo.lock"
if (Test-Path $CargoLock) {
  $CargoLockText = Get-Content $CargoLock -Raw
  if ($CargoLockText -match 'name = "cc"[\s\S]{0,120}version = "1\.2\.75"') {
    Write-Host "Normalizing Rust dependency cc 1.2.75 -> 1.2.65 for mirror compatibility..." -ForegroundColor Cyan
    Push-Location "desktop\src-tauri"
    try {
      & cargo update -p cc@1.2.75 --precise 1.2.65
      Assert-NativeSuccess "cargo dependency normalization"
    }
    finally { Pop-Location }
  }
}

Push-Location desktop
try {
  & npm ci
  Assert-NativeSuccess "npm ci"

  & npm run build
  Assert-NativeSuccess "frontend build"

  & npm run tauri build
  Assert-NativeSuccess "Tauri build"
}
finally {
  Pop-Location
}

$BundleDir = Join-Path $Root "desktop\src-tauri\target\release\bundle"
if (-not (Test-Path $BundleDir)) {
  throw "Build command finished but bundle directory was not created: $BundleDir"
}

$Installers = Get-ChildItem $BundleDir -Recurse -File -ErrorAction SilentlyContinue |
  Where-Object { $_.Extension -in @(".msi", ".exe") }
if (-not $Installers) {
  throw "Build command finished but no MSI/NSIS installer was found under: $BundleDir"
}

$PrimaryInstaller = $Installers | Select-Object -First 1
Write-Host "Primary installer: $($PrimaryInstaller.FullName)" -ForegroundColor Cyan

Write-Host ""
Write-Host "Build completed successfully." -ForegroundColor Green
Write-Host "Installers:" -ForegroundColor Green
$Installers | ForEach-Object { Write-Host "  $($_.FullName)" }
