$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Assert-NativeSuccess {
  param([Parameter(Mandatory = $true)][string]$Step)
  if ($LASTEXITCODE -ne 0) {
    throw "$Step failed with exit code $LASTEXITCODE"
  }
}

Write-Host "[1/2] Svelte frontend verification"
Push-Location desktop
try {
  npm ci
  Assert-NativeSuccess "npm ci"
  npm run build
  Assert-NativeSuccess "frontend build"
  npx svelte-check --tsconfig ./tsconfig.json
  Assert-NativeSuccess "svelte-check"
}
finally {
  Pop-Location
}

Write-Host "[2/2] Rust/Tauri verification"
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
  throw "cargo not found; install Rust with rustup before running product verification."
}
Push-Location desktop\src-tauri
try {
  cargo fmt --check
  Assert-NativeSuccess "cargo fmt"
  cargo check
  Assert-NativeSuccess "cargo check"
  cargo test --bin video-notes-ai native_engine -- --nocapture
  Assert-NativeSuccess "native engine tests"
}
finally {
  Pop-Location
}

Write-Host "All enabled product verification gates passed." -ForegroundColor Green
