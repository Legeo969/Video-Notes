param(
  [string]$OutputDir = ".build\runtime-payload-sources",
  [string]$PythonExe = "python",
  [string]$FfmpegDir = "",
  [string]$WhisperCppDir = "",
  [string]$TesseractDir = "",
  [switch]$SkipInstall,
  [switch]$IncludeTranscriptionCpu,
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

function Resolve-WhisperCppTools {
  param([Parameter(Mandatory = $true)][string]$Destination)
  if ($WhisperCppDir.Trim()) {
    $candidate = Resolve-PathStrict $WhisperCppDir
    $releaseDir = Join-Path $candidate "Release"
    if (Test-Path (Join-Path $releaseDir "whisper-cli.exe")) {
      return $releaseDir
    }
    return $candidate
  }
  if ($SkipInstall) {
    throw "-SkipInstall was requested, but -WhisperCppDir was not provided."
  }
  $zipPath = Join-Path $OutputRoot "whisper-bin-x64.zip"
  $extractRoot = Join-Path $OutputRoot "whisper-cpp-extract"
  if (Test-Path $extractRoot) {
    Remove-Item -LiteralPath $extractRoot -Recurse -Force
  }
  Write-Host "Downloading whisper.cpp standalone tools..." -ForegroundColor Cyan
  Invoke-WebRequest -Uri "https://github.com/ggml-org/whisper.cpp/releases/latest/download/whisper-bin-x64.zip" -OutFile $zipPath
  Expand-Archive -LiteralPath $zipPath -DestinationPath $extractRoot -Force
  $releaseDir = Join-Path $extractRoot "Release"
  if (-not (Test-Path (Join-Path $releaseDir "whisper-cli.exe"))) {
    throw "whisper.cpp release archive did not contain Release\whisper-cli.exe"
  }
  return $releaseDir
}

function Resolve-TesseractTools {
  if ($TesseractDir.Trim()) {
    return Resolve-PathStrict $TesseractDir
  }
  $tesseract = Get-Command tesseract -ErrorAction SilentlyContinue
  if (-not $tesseract) {
    throw "tesseract.exe was not found. Pass -TesseractDir with tesseract.exe and tessdata."
  }
  return Split-Path -Parent $tesseract.Source
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

$downloadToolsSource = Join-Path $OutputRoot "download-tools"
New-Item -ItemType Directory -Force $downloadToolsSource | Out-Null
$ytdlpExe = Join-Path $downloadToolsSource "yt-dlp.exe"
if (-not (Test-Path $ytdlpExe)) {
  if ($SkipInstall) {
    throw "-SkipInstall was requested, but yt-dlp.exe does not exist: $ytdlpExe"
  }
  Write-Host "Downloading yt-dlp standalone executable..." -ForegroundColor Cyan
  Invoke-WebRequest -Uri "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" -OutFile $ytdlpExe
}

$whisperCppSource = Join-Path $OutputRoot "whisper-cpp-tools"
New-Item -ItemType Directory -Force $whisperCppSource | Out-Null
$whisperCppDirPath = Resolve-WhisperCppTools $whisperCppSource
foreach ($item in @(
  "whisper-cli.exe",
  "whisper.dll",
  "ggml.dll",
  "ggml-base.dll",
  "ggml-cpu-alderlake.dll",
  "ggml-cpu-cannonlake.dll",
  "ggml-cpu-cascadelake.dll",
  "ggml-cpu-haswell.dll",
  "ggml-cpu-icelake.dll",
  "ggml-cpu-sandybridge.dll",
  "ggml-cpu-skylakex.dll",
  "ggml-cpu-sse42.dll",
  "ggml-cpu-x64.dll"
)) {
  Copy-RequiredItem (Join-Path $whisperCppDirPath $item) (Join-Path $whisperCppSource $item)
}

$tesseractSource = Join-Path $OutputRoot "tesseract-ocr-tools"
New-Item -ItemType Directory -Force $tesseractSource | Out-Null
$tesseractDirPath = Resolve-TesseractTools
Copy-RequiredItem (Join-Path $tesseractDirPath "tesseract.exe") (Join-Path $tesseractSource "tesseract.exe")
Copy-RequiredItem (Join-Path $tesseractDirPath "tessdata") (Join-Path $tesseractSource "tessdata")

$sidecarSite = New-IsolatedVenv "sidecar" @("requirements\sidecar.txt")
$transcriptionCpuSite = Join-Path $OutputRoot "transcription-cpu-site"
if ($IncludeTranscriptionCpu) {
  $transcriptionCpuSite = New-IsolatedVenv "transcription-cpu" @("requirements\transcription-cpu.txt")
}
$transcriptionCudaSite = Join-Path $OutputRoot "transcription-cuda-site"
if ($IncludeCuda) {
  $transcriptionCudaSite = New-IsolatedVenv "transcription-cuda" @("requirements\transcription-cpu.txt", "requirements\cuda.txt")
}

$sourceMap = [ordered]@{
  "base-engine" = $baseSource
  "download-tools" = $downloadToolsSource
  "ffmpeg-tools" = $ffmpegSource
  "whisper-cpp-tools" = $whisperCppSource
  "tesseract-ocr-tools" = $tesseractSource
  "transcription-cpu" = $transcriptionCpuSite
  "transcription-cuda" = $transcriptionCudaSite
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
  $componentsToStage = @(
    "base-engine",
    "download-tools",
    "ffmpeg-tools",
    "whisper-cpp-tools",
    "tesseract-ocr-tools"
  )
  if ($IncludeTranscriptionCpu) { $componentsToStage += "transcription-cpu" }
  if ($IncludeCuda) { $componentsToStage += "transcription-cuda" }
  if ($IncludeOcrCpu) { $componentsToStage += "ocr-cpu" }
  if ($IncludeOcrGpu) { $componentsToStage += "ocr-gpu" }
  $componentArgs = @()
  foreach ($component in $componentsToStage) {
    $componentArgs += @("--component", $component)
  }
  & python "$PSScriptRoot\stage_runtime_payloads.py" --source-map $mapPath --clean @componentArgs
  Assert-NativeSuccess "runtime payload staging"
  & python "$PSScriptRoot\verify_runtime_payloads.py" @componentArgs
  Assert-NativeSuccess "runtime payload readiness"
}
