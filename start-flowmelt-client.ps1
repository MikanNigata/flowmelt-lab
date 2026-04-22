param(
  [string]$ServerHost = $env:FLOWMELT_SERVER_HOST,
  [int]$ServerPort = 8443,
  [string]$PinSha256 = $env:FLOWMELT_PIN_SHA256,
  [string]$TokenFile = $env:FLOWMELT_TOKEN_FILE
)

$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
if (-not $TokenFile) {
  $TokenFile = Join-Path $Root "flowmelt-token.txt"
}
if (-not $ServerHost) {
  throw "Set -ServerHost or FLOWMELT_SERVER_HOST."
}
if (-not $PinSha256) {
  throw "Set -PinSha256 or FLOWMELT_PIN_SHA256."
}

$LogFile = Join-Path $Root "flowmelt-client.log"
$ErrFile = Join-Path $Root "flowmelt-client.err.log"

$Existing = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 1080 -State Listen -ErrorAction SilentlyContinue
if ($Existing) {
  Write-Host "FlowMelt SOCKS5 already appears to be listening on 127.0.0.1:1080."
  exit 0
}

$Process = Start-Process -FilePath python -ArgumentList @(
  "flowmelt\flowmelt_client.py",
  "--listen-host", "127.0.0.1",
  "--listen-port", "1080",
  "--server-host", $ServerHost,
  "--server-port", "$ServerPort",
  "--token-file", $TokenFile,
  "--pin-sha256", $PinSha256
) -WorkingDirectory $Root -RedirectStandardOutput $LogFile -RedirectStandardError $ErrFile -PassThru

Start-Sleep -Seconds 1
if ($Process.HasExited) {
  Write-Error "FlowMelt client exited. See $ErrFile"
}

Write-Host "FlowMelt SOCKS5 listening on 127.0.0.1:1080 (pid $($Process.Id))."
