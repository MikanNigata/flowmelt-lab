$Connections = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 1080 -State Listen -ErrorAction SilentlyContinue
if (-not $Connections) {
  Write-Host "FlowMelt SOCKS5 is not listening on 127.0.0.1:1080."
  exit 0
}

$Connections |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object {
    Stop-Process -Id $_ -Force
    Write-Host "Stopped FlowMelt client pid $_."
  }
