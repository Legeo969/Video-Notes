param(
  [switch]$SkipRust
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "[1/3] Python backend verification"
python -m pytest -q `
  --ignore=tests/test_collection_delete.py `
  --ignore=tests/test_smart_summary.py `
  --ignore=tests/test_provider_profile_settings.py

Write-Host "[2/3] Svelte frontend verification"
Push-Location desktop
try {
  npm ci
  npm run build
  npx svelte-check --tsconfig ./tsconfig.json
}
finally {
  Pop-Location
}

Write-Host "[3/3] Rust/Tauri verification"
if (Get-Command cargo -ErrorAction SilentlyContinue) {
  Push-Location desktop\src-tauri
  try {
    cargo fmt --check
    cargo check
    cargo test
  }
  finally {
    Pop-Location
  }
}
elif ($SkipRust) {
  Write-Warning "cargo not found; Rust/Tauri verification was explicitly skipped with -SkipRust. This is not a release-ready verification result."
}
else {
  Write-Host ""
  Write-Host "Rust/Cargo is required to compile the Tauri desktop shell." -ForegroundColor Yellow
  Write-Host "Install Rust with rustup, then reopen PowerShell and confirm:" -ForegroundColor Yellow
  Write-Host "  rustc --version"
  Write-Host "  cargo --version"
  Write-Host "On Windows, also install Visual Studio Build Tools with the 'Desktop development with C++' workload."
  Write-Host "To run only Python/frontend checks intentionally, use:"
  Write-Host "  .\scripts\verify_product.ps1 -SkipRust"
  throw "cargo not found; full product verification cannot continue."
}

Write-Host "All enabled product verification gates passed." -ForegroundColor Green
