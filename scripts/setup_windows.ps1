[CmdletBinding()]
param(
    [string]$VenvName = ".venv",
    [string]$PythonVersion = "3.11"
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "[*] $Message" -ForegroundColor Cyan
}

function Fail {
    param([string]$Message)
    Write-Host ""
    Write-Host "[X] $Message" -ForegroundColor Red
    exit 1
}

function Get-PythonVersion {
    param(
        [string]$Command,
        [string[]]$Args
    )

    $output = & $Command @($Args + @("-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")) 2>$null
    if ($LASTEXITCODE -ne 0) {
        return $null
    }

    return $output.Trim()
}

function Resolve-PythonCommand {
    param([string]$RequestedVersion)

    $candidates = @(
        @{ Label = "py -$RequestedVersion"; Command = "py"; Args = @("-$RequestedVersion") },
        @{ Label = "py -3.10"; Command = "py"; Args = @("-3.10") },
        @{ Label = "python"; Command = "python"; Args = @() }
    )

    foreach ($candidate in $candidates) {
        try {
            $version = Get-PythonVersion -Command $candidate.Command -Args $candidate.Args
            if ($version -in @("3.10", "3.11")) {
                return $candidate
            }
        } catch {
            continue
        }
    }

    return $null
}

function Invoke-Python {
    param(
        [hashtable]$PythonSpec,
        [string[]]$Args
    )

    & $PythonSpec.Command @($PythonSpec.Args + $Args)
    if ($LASTEXITCODE -ne 0) {
        Fail "Perintah Python gagal: $($PythonSpec.Label) $($Args -join ' ')"
    }
}

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

Write-Host "SafePick - Windows Setup" -ForegroundColor Green
Write-Host "Project root: $projectRoot"

$python = Resolve-PythonCommand -RequestedVersion $PythonVersion
if (-not $python) {
    Fail "Python 3.10 atau 3.11 tidak ditemukan. Install Python 3.11 lalu jalankan script ini lagi."
}

Write-Step "Menggunakan interpreter: $($python.Label)"

$venvPath = Join-Path $projectRoot $VenvName
$venvPython = Join-Path $venvPath "Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Step "Membuat virtual environment di $VenvPath"
    Invoke-Python -PythonSpec $python -Args @("-m", "venv", $VenvName)
} else {
    Write-Step "Virtual environment sudah ada di $VenvPath"
}

Write-Step "Upgrade pip, setuptools, wheel"
& $venvPython -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) {
    Fail "Gagal upgrade pip/setuptools/wheel."
}

Write-Step "Install dependency project"
& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Fail "Gagal install requirements.txt"
}

Write-Step "Membersihkan konflik OpenCV headless"
& $venvPython -m pip uninstall -y opencv-python-headless | Out-Null
& $venvPython -m pip uninstall -y opencv-python | Out-Null
& $venvPython -m pip install opencv-python==4.8.1.78
if ($LASTEXITCODE -ne 0) {
    Fail "Gagal install opencv-python GUI build."
}

Write-Step "Verifikasi environment"
& $venvPython -c "import cv2, sys; gui=[l for l in cv2.getBuildInformation().splitlines() if 'GUI:' in l][0].strip(); print('Python:', sys.version.split()[0]); print('OpenCV:', cv2.__version__); print(gui)"
if ($LASTEXITCODE -ne 0) {
    Fail "Verifikasi OpenCV gagal."
}

Write-Step "Inisialisasi database"
Write-Host "Pastikan Apache/MySQL XAMPP sudah aktif sebelum langkah ini berjalan."
& $venvPython scripts\db_tools\init_database.py
if ($LASTEXITCODE -ne 0) {
    Fail "Inisialisasi database gagal. Pastikan MySQL/MariaDB aktif dan kredensial database sudah benar."
}

Write-Host ""
Write-Host "[OK] Setup selesai." -ForegroundColor Green
Write-Host "Jalankan web server dengan:"
Write-Host "    start_web.bat"
