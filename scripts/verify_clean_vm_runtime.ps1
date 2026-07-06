param(
  [string]$AppDir = "C:\Program Files\Video Notes AI",
  [string]$Installer = "",
  [switch]$Json
)

$ErrorActionPreference = "Stop"

function New-Result {
  param(
    [Parameter(Mandatory = $true)][bool]$Ok,
    [string]$AppExe = "",
    [string]$Installer = "",
    [object[]]$Errors = @()
  )
  return [ordered]@{
    ok = $Ok
    app_exe = $AppExe
    installer = $Installer
    errors = $Errors
  }
}

function Add-Error {
  param(
    [object[]]$Errors = @(),
    [Parameter(Mandatory = $true)][string]$Code,
    [Parameter(Mandatory = $true)][string]$Message,
    [string]$Path = ""
  )
  $Errors += [ordered]@{
    code = $Code
    message = $Message
    path = $Path
  }
  return $Errors
}

function Find-AppExe {
  param([Parameter(Mandatory = $true)][string]$Root)
  $exact = Join-Path $Root "Video Notes AI.exe"
  if (Test-Path -LiteralPath $exact -PathType Leaf) {
    return (Resolve-Path -LiteralPath $exact).Path
  }
  $candidate = Get-ChildItem -LiteralPath $Root -Filter "*.exe" -File -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if ($candidate) {
    return $candidate.FullName
  }
  return ""
}

$errors = @()
$appExe = ""
$resolvedInstaller = ""

if ($AppDir.Trim()) {
  if (Test-Path -LiteralPath $AppDir -PathType Container) {
    $resolvedAppDir = (Resolve-Path -LiteralPath $AppDir).Path
    $appExe = Find-AppExe $resolvedAppDir
    if (-not $appExe) {
      $errors = Add-Error $errors "app_exe_missing" "could not locate app executable" $resolvedAppDir
    }
  }
  else {
    $errors = Add-Error $errors "app_dir_missing" "app directory is missing" $AppDir
  }
}

if ($Installer.Trim()) {
  if (Test-Path -LiteralPath $Installer -PathType Leaf) {
    $resolvedInstaller = (Resolve-Path -LiteralPath $Installer).Path
    $extension = [IO.Path]::GetExtension($resolvedInstaller).ToLowerInvariant()
    if ($extension -notin @(".exe", ".msi")) {
      $errors = Add-Error $errors "installer_extension" "installer must be an .exe or .msi artifact" $resolvedInstaller
    }
  }
  else {
    $errors = Add-Error $errors "installer_missing" "installer file is missing" $Installer
  }
}

$result = New-Result ($errors.Count -eq 0) $appExe $resolvedInstaller $errors
if ($Json) {
  $result | ConvertTo-Json -Depth 5
}
else {
  if ($result.ok) {
    Write-Host "Clean VM runtime: OK" -ForegroundColor Green
  }
  else {
    Write-Host "Clean VM runtime: FAILED" -ForegroundColor Red
    foreach ($issue in $errors) {
      Write-Host "- [$($issue.code)] $($issue.message) $($issue.path)"
    }
  }
}

if ($result.ok) { exit 0 }
exit 1
