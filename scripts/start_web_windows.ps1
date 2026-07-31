$ErrorActionPreference = "Stop"

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $projectRoot

$port = if ($env:SAFEPICK_WEB_PORT) { [int]$env:SAFEPICK_WEB_PORT } elseif ($env:FACEGATE_WEB_PORT) { [int]$env:FACEGATE_WEB_PORT } else { 8000 }
$listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1

$lanIp = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object {
        $_.IPAddress -notlike "127.*" -and
        $_.IPAddress -notlike "169.254.*" -and
        $_.PrefixOrigin -ne "WellKnown"
    } |
    Sort-Object InterfaceMetric |
    Select-Object -First 1 -ExpandProperty IPAddress

if (-not $lanIp) {
    $lanIp = "IP-KOMPUTER"
}

Write-Host "======================================================="
Write-Host " SafePick Web Server"
Write-Host "======================================================="
Write-Host ""
Write-Host "Project : $projectRoot"
Write-Host "Port    : $port"
Write-Host ""
Write-Host "URL di device ini:"
Write-Host "  Display : http://127.0.0.1:$port/display"
Write-Host "  Admin   : http://127.0.0.1:$port/admin"
Write-Host ""
Write-Host "URL dari HP/laptop lain satu jaringan:"
Write-Host "  Display : http://$lanIp`:$port/display"
Write-Host "  Admin   : http://$lanIp`:$port/admin"
Write-Host ""

if ($listener) {
    $process = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
    $name = if ($process) { $process.ProcessName } else { "unknown" }
    Write-Host "[!] Port $port sudah dipakai oleh PID $($listener.OwningProcess) ($name)."
    Write-Host "    Jalankan stop_web.bat dulu kalau ingin restart server."
    exit 1
}

Write-Host "[*] Pastikan MySQL/MariaDB sudah aktif sebelum memakai admin/display."
Write-Host "[*] Tekan CTRL+C untuk mematikan server dari jendela ini."
Write-Host ""

python -m uvicorn backend.app:app --host 0.0.0.0 --port $port
