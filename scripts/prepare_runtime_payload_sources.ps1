param(
  [string]$OutputDir = ".build\runtime-payload-sources",
  [string]$PythonExe = "python",
  [string]$FfmpegDir = "",
  [switch]$SkipInstall,
  [switch]$IncludeCuda,
  [switch]$IncludeOcrCpu,
  [switch]$IncludeOcrGpu,
  [switch]$StagePayloads
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

function Resolve-PathStrict {
  param([Parameter(Mandatory = $true)][string]$PathValue)
  $resolved = Resolve-Path -LiteralPath $PathValue -ErrorAction Stop
  return $resolved.Path
}

function Read-PythonInfo {
  param([Parameter(Mandatory = $true)][string]$Executable)
  $json = & $Executable -c "import json,sys,sysconfig; print(json.dumps({'executable':sys.executable,'prefix':sys.prefix,'base_prefix':sys.base_prefix,'version_nodot':f'{sys.version_info.major}{sys.version_info.minor}','purelib':sysconfig.get_paths()['purelib']}))"
  Assert-NativeSuccess "python runtime discovery"
  return $json | ConvertFrom-Json
}

function Get-VenvPython {
  param([Parameter(Mandatory = $true)][string]$VenvRoot)
  return Join-Path $VenvRoot "Scripts\python.exe"
}

function New-IsolatedVenv {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string[]]$RequirementFiles
  )
  $venvRoot = Join-Path $OutputRoot "venv-$Name"
  $venvPython = Get-VenvPython $venvRoot
  if (-not (Test-Path $venvPython)) {
    if ($SkipInstall) {
      throw "-SkipInstall was requested, but the isolated runtime environment does not exist: $venvPython"
    }
    Write-Host "Creating isolated runtime environment: $Name" -ForegroundColor Cyan
    & $PythonExe -m venv $venvRoot
    Assert-NativeSuccess "$Name virtual environment creation"
  }
  if (-not $SkipInstall) {
    Write-Host "Installing runtime dependencies: $Name" -ForegroundColor Cyan
    & $venvPython -m pip install --upgrade pip wheel "setuptools<81"
    Assert-NativeSuccess "$Name build-tool installation"
    foreach ($requirement in $RequirementFiles) {
      & $venvPython -m pip install -r $requirement
      Assert-NativeSuccess "$Name dependency installation from $requirement"
    }
  }
  $site = & $venvPython -c "import sysconfig; print(sysconfig.get_paths()['purelib'])"
  Assert-NativeSuccess "$Name site-packages discovery"
  return $site.Trim()
}

function Copy-RequiredItem {
  param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Destination
  )
  if (-not (Test-Path $Source)) {
    throw "Required runtime source is missing: $Source"
  }
  if (Test-Path $Destination) {
    Remove-Item -LiteralPath $Destination -Recurse -Force
  }
  $parent = Split-Path -Parent $Destination
  New-Item -ItemType Directory -Force $parent | Out-Null
  Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -Force
}

function Copy-PythonStdlib {
  param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Destination
  )
  if (-not (Test-Path $Source)) {
    throw "Required Python stdlib source is missing: $Source"
  }
  if (Test-Path $Destination) {
    Remove-Item -LiteralPath $Destination -Recurse -Force
  }
  New-Item -ItemType Directory -Force $Destination | Out-Null
  Get-ChildItem -LiteralPath $Source -Force | Where-Object {
    $_.Name -ne "site-packages"
  } | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $Destination $_.Name) -Recurse -Force
  }
}

function Resolve-FfmpegTools {
  if ($FfmpegDir.Trim()) {
    return Resolve-PathStrict $FfmpegDir
  }
  $ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
  $ffprobe = Get-Command ffprobe -ErrorAction SilentlyContinue
  if (-not $ffmpeg -or -not $ffprobe) {
    throw "ffmpeg and ffprobe were not found. Pass -FfmpegDir with both binaries."
  }
  $ffmpegDirPath = Split-Path -Parent $ffmpeg.Source
  $ffprobeDirPath = Split-Path -Parent $ffprobe.Source
  if ($ffmpegDirPath -ne $ffprobeDirPath) {
    throw "ffmpeg and ffprobe must come from the same directory. Pass -FfmpegDir explicitly."
  }
  return $ffmpegDirPath
}

if (-not (Get-Command $PythonExe -ErrorAction SilentlyContinue)) {
  throw "$PythonExe not found"
}

$OutputRoot = if ([IO.Path]::IsPathRooted($OutputDir)) {
  $OutputDir
} else {
  Join-Path $Root $OutputDir
}
New-Item -ItemType Directory -Force $OutputRoot | Out-Null

$pythonInfo = Read-PythonInfo $PythonExe
$baseSource = Join-Path $OutputRoot "base-engine"
New-Item -ItemType Directory -Force $baseSource | Out-Null
Copy-RequiredItem $pythonInfo.executable (Join-Path $baseSource "python.exe")
Copy-RequiredItem (Join-Path $pythonInfo.base_prefix "python3.dll") (Join-Path $baseSource "python3.dll")
Copy-RequiredItem (Join-Path $pythonInfo.base_prefix "python$($pythonInfo.version_nodot).dll") (Join-Path $baseSource "python$($pythonInfo.version_nodot).dll")
Copy-PythonStdlib (Join-Path $pythonInfo.base_prefix "Lib") (Join-Path $baseSource "Lib")
Copy-RequiredItem (Join-Path $pythonInfo.base_prefix "DLLs") (Join-Path $baseSource "DLLs")

$ffmpegSource = Join-Path $OutputRoot "ffmpeg-tools"
New-Item -ItemType Directory -Force $ffmpegSource | Out-Null
$ffmpegDirPath = Resolve-FfmpegTools
Copy-RequiredItem (Join-Path $ffmpegDirPath "ffmpeg.exe") (Join-Path $ffmpegSource "ffmpeg.exe")
Copy-RequiredItem (Join-Path $ffmpegDirPath "ffprobe.exe") (Join-Path $ffmpegSource "ffprobe.exe")

$sidecarRequirements = @("requirements\sidecar.txt")
if ($IncludeCuda) {
  $sidecarRequirements += "requirements\cuda.txt"
}
$sidecarSite = New-IsolatedVenv "sidecar" $sidecarRequirements

$sourceMap = [ordered]@{
  "base-engine" = $baseSource
  "ffmpeg-tools" = $ffmpegSource
  "transcription-cpu" = $sidecarSite
  "transcription-cuda" = $sidecarSite
}

if ($IncludeOcrCpu) {
  $ocrCpuSite = New-IsolatedVenv "ocr-cpu" @("requirements\ocr-cpu.txt")
  $sourceMap["ocr-cpu"] = $ocrCpuSite
}
else {
  $sourceMap["ocr-cpu"] = Join-Path $OutputRoot "ocr-cpu-site"
}

if ($IncludeOcrGpu) {
  $ocrGpuSite = New-IsolatedVenv "ocr-gpu" @("requirements\ocr-gpu.txt")
  $sourceMap["ocr-gpu"] = $ocrGpuSite
}
else {
  $sourceMap["ocr-gpu"] = Join-Path $OutputRoot "ocr-gpu-site"
}

$mapPath = Join-Path $OutputRoot "payload-source-map.json"
$sourceMap | ConvertTo-Json -Depth 3 | Set-Content -Path $mapPath -Encoding utf8
Write-Host "Runtime payload source map: $mapPath" -ForegroundColor Green

if ($StagePayloads) {
  & python "$PSScriptRoot\stage_runtime_payloads.py" --source-map $mapPath --clean
  Assert-NativeSuccess "runtime payload staging"
  & python "$PSScriptRoot\verify_runtime_payloads.py"
  Assert-NativeSuccess "runtime payload readiness"
}
