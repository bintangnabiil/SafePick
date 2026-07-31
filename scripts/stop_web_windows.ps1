$ErrorActionPreference = "Stop"

$port = if ($env:SAFEPICK_WEB_PORT) { [int]$env:SAFEPICK_WEB_PORT } elseif ($env:FACEGATE_WEB_PORT) { [int]$env:FACEGATE_WEB_PORT } else { 8000 }
$listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue

if (-not $listeners) {
    Write-Host "[OK] Tidak ada web server yang listen di port $port."
    exit 0
}

foreach ($listener in $listeners) {
    $process = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
    $name = if ($process) { $process.ProcessName } else { "unknown" }
    Write-Host "[*] Mematikan PID $($listener.OwningProcess) ($name) di port $port..."
    Stop-Process -Id $listener.OwningProcess -Force
}

Start-Sleep -Seconds 1
$stillRunning = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($stillRunning) {
    Write-Host "[X] Port $port masih aktif. Cek proses secara manual."
    exit 1
}

Write-Host "[OK] Web server di port $port sudah dimatikan."
