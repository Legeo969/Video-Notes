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

foreach ($command in @("python", "node", "npm", "rustc", "cargo")) {
  if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
    throw "$command not found"
  }
}

$rustInfo = & rustc -vV
Assert-NativeSuccess "rustc -vV"
$hostLine = $rustInfo | Select-String '^host:' | Select-Object -First 1
if (-not $hostLine) { throw "Could not read Rust host target triple" }
$targetTriple = ($hostLine.ToString() -replace '^host:\s*', '').Trim()
$sidecar = Join-Path $Root "desktop\src-tauri\binaries\python-engine-$targetTriple.exe"
$sidecarFingerprintFile = "$sidecar.fingerprint"
$currentSidecarFingerprint = (& python "$PSScriptRoot\compute_sidecar_fingerprint.py").Trim()
Assert-NativeSuccess "sidecar source fingerprint"

if ($ReuseSidecar) {
  if ($IncludeCuda) {
    Write-Warning "-IncludeCuda requires rebuilding the sidecar so CUDA DLLs are packaged. Ignoring -ReuseSidecar."
    $ReuseSidecar = $false
  }
}

if ($ReuseSidecar) {
  if (-not (Test-Path $sidecar)) {
    throw "-ReuseSidecar was requested, but the staged sidecar does not exist: $sidecar"
  }
  $sidecarSize = (Get-Item $sidecar).Length
  $sidecarMiB = [math]::Round($sidecarSize / 1MB, 1)
  if ($sidecarSize -gt 1700MB) {
    Write-Warning "Existing sidecar is $sidecarMiB MiB and is too large for NSIS. Rebuilding it in the isolated environment instead of reusing it."
    $ReuseSidecar = $false
  }
  elseif (-not (Test-Path $sidecarFingerprintFile)) {
    Write-Warning "The staged sidecar has no source fingerprint and may contain stale Python code. Rebuilding it now."
    $ReuseSidecar = $false
  }
  else {
    $stagedFingerprint = (Get-Content $sidecarFingerprintFile -Raw).Trim()
    if ($stagedFingerprint -ne $currentSidecarFingerprint) {
      Write-Warning "Python backend sources changed after the sidecar was built. Rebuilding the sidecar instead of reusing stale code."
      $ReuseSidecar = $false
    }
    else {
      Write-Host "Reusing sidecar: $sidecar ($sidecarMiB MiB)" -ForegroundColor Cyan
    }
  }
}

if (-not $ReuseSidecar) {
  & "$PSScriptRoot\prepare_tauri_sidecar.ps1" -SkipInstall:$SkipSidecarInstall -IncludeCuda:$IncludeCuda -TargetTriple $targetTriple
  Assert-NativeSuccess "Python sidecar preparation"
}

# Final preflight before invoking Tauri/NSIS.
if (-not (Test-Path $sidecar)) {
  throw "Staged sidecar was not created: $sidecar"
}
$finalSize = (Get-Item $sidecar).Length
if ($finalSize -gt 1700MB) {
  $finalMiB = [math]::Round($finalSize / 1MB, 1)
  throw "Sidecar is too large for NSIS: $finalMiB MiB. Run without -ReuseSidecar to rebuild it in the isolated environment."
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
& python "$PSScriptRoot\verify_installed_runtime.py" --sidecar $sidecar --installer $PrimaryInstaller.FullName --timeout 180
Assert-NativeSuccess "installed runtime sidecar smoke"

Write-Host ""
Write-Host "Build completed successfully." -ForegroundColor Green
Write-Host "Installers:" -ForegroundColor Green
$Installers | ForEach-Object { Write-Host "  $($_.FullName)" }
