param(
  [string]$OutputDir = ".build\runtime-payload-sources",
  [string]$FfmpegDir = "",
  [string]$WhisperCppDir = "",
  [string]$TesseractDir = "",
  [switch]$SkipInstall,
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

$OutputRoot = if ([IO.Path]::IsPathRooted($OutputDir)) {
  $OutputDir
} else {
  Join-Path $Root $OutputDir
}
New-Item -ItemType Directory -Force $OutputRoot | Out-Null

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
$whisperCppDirPath = Resolve-WhisperCppTools
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

$sourceMap = [ordered]@{
  "download-tools" = $downloadToolsSource
  "ffmpeg-tools" = $ffmpegSource
  "whisper-cpp-tools" = $whisperCppSource
  "tesseract-ocr-tools" = $tesseractSource
}

$mapPath = Join-Path $OutputRoot "payload-source-map.json"
$sourceMap | ConvertTo-Json -Depth 3 | Set-Content -Path $mapPath -Encoding utf8
Write-Host "Runtime payload source map: $mapPath" -ForegroundColor Green

if ($StagePayloads) {
  $componentArgs = @()
  foreach ($component in $sourceMap.Keys) {
    $componentArgs += @("--component", $component)
  }
  & python "$PSScriptRoot\stage_runtime_payloads.py" --source-map $mapPath --clean @componentArgs
  Assert-NativeSuccess "runtime payload staging"
  & python "$PSScriptRoot\verify_runtime_payloads.py" @componentArgs
  Assert-NativeSuccess "runtime payload readiness"
}
