param(
  [string]$AppDir = "C:\Program Files\Video Notes AI",
  [string]$Sidecar = "",
  [string]$Installer = "",
  [int]$TimeoutSeconds = 60,
  [switch]$Json
)

$ErrorActionPreference = "Stop"

function New-Result {
  param(
    [Parameter(Mandatory = $true)][bool]$Ok,
    [string]$AppExe = "",
    [string]$Sidecar = "",
    [string]$Installer = "",
    [object[]]$Errors = @()
  )
  return [ordered]@{
    ok = $Ok
    app_exe = $AppExe
    sidecar = $Sidecar
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
    Where-Object { $_.Name -notlike "python-engine*" } |
    Select-Object -First 1
  if ($candidate) {
    return $candidate.FullName
  }
  return ""
}

function Find-Sidecar {
  param([Parameter(Mandatory = $true)][string]$Root)
  $names = @(
    "python-engine.exe",
    "binaries\python-engine.exe",
    "resources\binaries\python-engine.exe"
  )
  foreach ($name in $names) {
    $candidate = Join-Path $Root $name
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
      return (Resolve-Path -LiteralPath $candidate).Path
    }
  }
  $match = Get-ChildItem -LiteralPath $Root -Recurse -Filter "python-engine*.exe" -File -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if ($match) {
    return $match.FullName
  }
  return ""
}

function Read-FramedMessages {
  param([Parameter(Mandatory = $true)][byte[]]$Data)
  $messages = @()
  $offset = 0
  while ($offset -lt $Data.Length) {
    $headerEnd = -1
    for ($i = $offset; $i -le $Data.Length - 4; $i++) {
      if ($Data[$i] -eq 13 -and $Data[$i + 1] -eq 10 -and $Data[$i + 2] -eq 13 -and $Data[$i + 3] -eq 10) {
        $headerEnd = $i
        break
      }
    }
    if ($headerEnd -lt 0) {
      $tail = [Text.Encoding]::UTF8.GetString($Data, $offset, $Data.Length - $offset).Trim()
      if ($tail.Length -gt 0) {
        throw "stdout contains non-framed trailing data"
      }
      break
    }
    $header = [Text.Encoding]::UTF8.GetString($Data, $offset, $headerEnd - $offset)
    $contentLength = $null
    foreach ($line in $header -split "`r`n") {
      if ($line.ToLowerInvariant().StartsWith("content-length:")) {
        $contentLength = [int]$line.Split(":", 2)[1].Trim()
        break
      }
    }
    if ($null -eq $contentLength) {
      throw "missing Content-Length header"
    }
    $bodyStart = $headerEnd + 4
    $bodyEnd = $bodyStart + $contentLength
    if ($bodyEnd -gt $Data.Length) {
      throw "incomplete frame body"
    }
    $body = [Text.Encoding]::UTF8.GetString($Data, $bodyStart, $contentLength)
    $messages += ($body | ConvertFrom-Json)
    $offset = $bodyEnd
  }
  return $messages
}

function Invoke-SidecarPing {
  param(
    [Parameter(Mandatory = $true)][string]$SidecarPath,
    [Parameter(Mandatory = $true)][int]$TimeoutSeconds
  )
  $psi = [Diagnostics.ProcessStartInfo]::new()
  $psi.FileName = $SidecarPath
  $psi.ArgumentList.Add("--stdio")
  $psi.UseShellExecute = $false
  $psi.RedirectStandardInput = $true
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.CreateNoWindow = $true

  $keepPath = Join-Path $env:SystemRoot "System32"
  $psi.Environment["PATH"] = $keepPath
  foreach ($key in @("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "VIDEO_NOTES_ENGINE", "VIDEO_NOTES_ENGINE_CWD", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")) {
    if ($psi.Environment.ContainsKey($key)) {
      $psi.Environment.Remove($key) | Out-Null
    }
  }

  $proc = [Diagnostics.Process]::new()
  $proc.StartInfo = $psi
  if (-not $proc.Start()) {
    throw "failed to start sidecar"
  }

  $request = @{
    jsonrpc = "2.0"
    protocol_version = 1
    id = 1
    method = "system.ping"
    params = @{}
  } | ConvertTo-Json -Compress
  $body = [Text.Encoding]::UTF8.GetBytes($request)
  $header = [Text.Encoding]::UTF8.GetBytes("Content-Length: $($body.Length)`r`n`r`n")
  $proc.StandardInput.BaseStream.Write($header, 0, $header.Length)
  $proc.StandardInput.BaseStream.Write($body, 0, $body.Length)
  $proc.StandardInput.BaseStream.Flush()
  $proc.StandardInput.Close()

  if (-not $proc.WaitForExit($TimeoutSeconds * 1000)) {
    try { $proc.Kill($true) } catch { $proc.Kill() }
    throw "sidecar did not exit within $TimeoutSeconds seconds"
  }
  if ($proc.ExitCode -ne 0) {
    $stderr = $proc.StandardError.ReadToEnd()
    throw "sidecar exited with code $($proc.ExitCode): $stderr"
  }

  $stream = [IO.MemoryStream]::new()
  $proc.StandardOutput.BaseStream.CopyTo($stream)
  $messages = Read-FramedMessages $stream.ToArray()
  foreach ($message in $messages) {
    if ($message.id -eq 1 -and $message.result -eq "pong") {
      return
    }
  }
  throw "system.ping response was not received"
}

$errors = @()
$resolvedAppDir = ""
$appExe = ""
$resolvedSidecar = ""
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

if ($Sidecar.Trim()) {
  if (Test-Path -LiteralPath $Sidecar -PathType Leaf) {
    $resolvedSidecar = (Resolve-Path -LiteralPath $Sidecar).Path
  }
  else {
    $errors = Add-Error $errors "sidecar_missing" "sidecar file is missing" $Sidecar
  }
}
elseif ($resolvedAppDir) {
  $resolvedSidecar = Find-Sidecar $resolvedAppDir
  if (-not $resolvedSidecar) {
    $errors = Add-Error $errors "sidecar_missing" "could not locate bundled python-engine sidecar" $resolvedAppDir
  }
}

if ($Installer.Trim()) {
  if (Test-Path -LiteralPath $Installer -PathType Leaf) {
    $resolvedInstaller = (Resolve-Path -LiteralPath $Installer).Path
  }
  else {
    $errors = Add-Error $errors "installer_missing" "installer file is missing" $Installer
  }
}

if ($resolvedSidecar) {
  try {
    Invoke-SidecarPing $resolvedSidecar $TimeoutSeconds
  }
  catch {
    $errors = Add-Error $errors "sidecar_ping_failed" $_.Exception.Message $resolvedSidecar
  }
}

$result = New-Result ($errors.Count -eq 0) $appExe $resolvedSidecar $resolvedInstaller $errors
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
