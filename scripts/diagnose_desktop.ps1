param(
  [string]$Executable,
  [string]$Shortcut = "$env:USERPROFILE\Desktop\Video Notes AI.lnk",
  [int]$WaitSeconds = 6
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$LogPath = Join-Path $env:LOCALAPPDATA "Video Notes AI\logs\desktop-startup.log"

function Resolve-ShortcutTarget([string]$Path) {
  if (-not (Test-Path $Path)) { return $null }
  $shell = New-Object -ComObject WScript.Shell
  $shortcutObject = $shell.CreateShortcut($Path)
  return $shortcutObject.TargetPath
}

if (-not $Executable) {
  $target = Resolve-ShortcutTarget $Shortcut
  if ($target) {
    $Executable = $target
    Write-Host "Shortcut target: $Executable"
  }
}

if (-not $Executable) {
  $candidates = @(
    (Join-Path $Root "desktop\src-tauri\target\release\video-notes-ai.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Video Notes AI\Video Notes AI.exe"),
    (Join-Path $env:ProgramFiles "Video Notes AI\Video Notes AI.exe")
  )
  $Executable = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}

if (-not $Executable -or -not (Test-Path $Executable)) {
  Write-Host "未找到程序文件。桌面快捷方式很可能指向了已经不存在的旧 EXE。" -ForegroundColor Red
  Write-Host "请重新安装，或传入正确路径："
  Write-Host "  .\scripts\diagnose_desktop.ps1 -Executable 'D:\path\Video Notes AI.exe'"
  exit 2
}

Write-Host "Starting: $Executable"
$before = Get-Date
$process = Start-Process -FilePath $Executable -PassThru
Start-Sleep -Seconds $WaitSeconds

if ($process.HasExited) {
  Write-Host "程序在启动后很快退出，ExitCode=$($process.ExitCode)" -ForegroundColor Red
} else {
  Write-Host "桌面进程仍在运行（PID=$($process.Id)）。" -ForegroundColor Green
}

if (Test-Path $LogPath) {
  Write-Host ""
  Write-Host "Startup log: $LogPath"
  Get-Content $LogPath -Tail 80
} else {
  Write-Host ""
  Write-Warning "没有生成启动日志。若这是旧版本，请先安装 v1.2.1 或更高版本。"
}
