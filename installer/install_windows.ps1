# ─────────────────────────────────────────────────────────────────────────────
# THOR Bitcoin Intelligence Dashboard — Windows Installer
# Wrapped into THOR-Setup.exe by build_windows.iss (Inno Setup)
# ─────────────────────────────────────────────────────────────────────────────
#Requires -Version 5.1

$DIST_URL    = "https://thor.rfbitcoin.com/dist/thor-latest.zip"
$NSSM_URL    = "https://nssm.cc/release/nssm-2.24.zip"
$INSTALL_DIR = "C:\THOR"
$SERVICE_NAME = "THOR"
$PORT = 5000

function Write-Step { param($msg) Write-Host "`n▶ $msg" -ForegroundColor Yellow }
function Write-OK   { param($msg) Write-Host "  ✓ $msg" -ForegroundColor Green }
function Write-Fail { param($msg) Write-Host "`n  ✗ $msg" -ForegroundColor Red }

Clear-Host
Write-Host @"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ⚡ THOR — Bitcoin Intelligence Dashboard
  Windows Installer
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"@ -ForegroundColor Yellow

# ── Admin check ───────────────────────────────────────────────────────────────
Write-Step "Checking administrator privileges..."
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "  Restarting with administrator privileges..." -ForegroundColor Cyan
    Start-Process powershell "-ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}
Write-OK "Running as administrator"

# ── Python check / install ────────────────────────────────────────────────────
Write-Step "Checking Python 3.9+..."
$python = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd -c "import sys; print(sys.version_info >= (3,9))" 2>$null
        if ($ver -eq "True") { $python = $cmd; break }
    } catch {}
}

if (-not $python) {
    Write-Host "  Python 3.9+ not found. Attempting automatic install..." -ForegroundColor Cyan

    # Try winget (available on Windows 10 1709+ with App Installer)
    $wingetOk = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        foreach ($pyId in @("Python.Python.3.11", "Python.Python.3.10", "Python.Python.3.9")) {
            Write-Host "  Trying winget: $pyId ..." -ForegroundColor Cyan
            winget install --id $pyId --silent --accept-package-agreements --accept-source-agreements 2>&1
            if ($LASTEXITCODE -eq 0) { $wingetOk = $true; break }
        }
    }

    if ($wingetOk) {
        # Refresh PATH
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                    [System.Environment]::GetEnvironmentVariable("Path","User")
        $python = "python"
        Write-OK "Python installed via winget"
    } else {
        Write-Fail "Could not auto-install Python."
        Write-Host ""
        Write-Host "  Please install Python 3.9 or later manually:" -ForegroundColor Cyan
        Write-Host "  https://www.python.org/downloads/" -ForegroundColor Cyan
        Write-Host "  Make sure to tick 'Add Python to PATH' during install." -ForegroundColor Cyan
        Write-Host "  Then re-run THOR-Setup.exe." -ForegroundColor Cyan
        Read-Host "`nPress Enter to exit"
        exit 1
    }
}
$pyver = & $python --version
Write-OK "Python: $pyver"

# ── Create install directory ──────────────────────────────────────────────────
Write-Step "Creating install directory..."
if (Test-Path $INSTALL_DIR) {
    try { Stop-Service $SERVICE_NAME -ErrorAction SilentlyContinue } catch {}
}
New-Item -ItemType Directory -Force -Path $INSTALL_DIR | Out-Null
Write-OK "Directory: $INSTALL_DIR"

# ── Download THOR ─────────────────────────────────────────────────────────────
Write-Step "Downloading THOR..."
$zipPath = "$env:TEMP\thor-latest.zip"
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $DIST_URL -OutFile $zipPath -UseBasicParsing
    Write-OK "Downloaded"
} catch {
    Write-Fail "Download failed: $_"
    Write-Host "  Check your internet connection and try again." -ForegroundColor Cyan
    Read-Host "`nPress Enter to exit"
    exit 1
}

# ── Extract ───────────────────────────────────────────────────────────────────
Write-Step "Extracting..."
Expand-Archive -LiteralPath $zipPath -DestinationPath $INSTALL_DIR -Force
Remove-Item $zipPath -Force
Write-OK "Extracted to $INSTALL_DIR"

# ── Create .env ───────────────────────────────────────────────────────────────
$envPath = "$INSTALL_DIR\.env"
if (-not (Test-Path $envPath)) {
    @"
KRAKEN_API_KEY=
KRAKEN_API_SECRET=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
THOR_PASSWORD_HASH=
THOR_PORT=$PORT
"@ | Out-File -FilePath $envPath -Encoding UTF8
}
Write-OK "Config created at $envPath"

# ── Python virtual environment ────────────────────────────────────────────────
Write-Step "Setting up Python environment (this may take a few minutes)..."
try {
    & $python -m venv "$INSTALL_DIR\venv"
    & "$INSTALL_DIR\venv\Scripts\pip.exe" install --quiet --upgrade pip
    & "$INSTALL_DIR\venv\Scripts\pip.exe" install --quiet -r "$INSTALL_DIR\requirements.txt"
    Write-OK "Dependencies installed"
} catch {
    Write-Fail "Failed to install Python dependencies: $_"
    Read-Host "`nPress Enter to exit"
    exit 1
}

# ── NSSM (service manager) ────────────────────────────────────────────────────
Write-Step "Installing Windows service..."
$nssmPath = "$INSTALL_DIR\tools\nssm.exe"
if (-not (Test-Path $nssmPath)) {
    try {
        $nssmZip = "$env:TEMP\nssm.zip"
        Invoke-WebRequest -Uri $NSSM_URL -OutFile $nssmZip -UseBasicParsing
        $nssmExtract = "$env:TEMP\nssm_extract"
        Expand-Archive -LiteralPath $nssmZip -DestinationPath $nssmExtract -Force
        New-Item -ItemType Directory -Force -Path "$INSTALL_DIR\tools" | Out-Null
        $arch = if ([Environment]::Is64BitOperatingSystem) { "win64" } else { "win32" }
        Copy-Item "$nssmExtract\nssm-2.24\$arch\nssm.exe" $nssmPath -Force
        Remove-Item $nssmZip, $nssmExtract -Recurse -Force
    } catch {
        Write-Fail "Could not download NSSM service manager: $_"
        Read-Host "`nPress Enter to exit"
        exit 1
    }
}

& $nssmPath stop $SERVICE_NAME 2>$null
& $nssmPath remove $SERVICE_NAME confirm 2>$null

& $nssmPath install $SERVICE_NAME "$INSTALL_DIR\venv\Scripts\python.exe" "server.py"
& $nssmPath set $SERVICE_NAME AppDirectory "$INSTALL_DIR\dashboard"
& $nssmPath set $SERVICE_NAME AppStdout "$INSTALL_DIR\logs\thor.log"
& $nssmPath set $SERVICE_NAME AppStderr "$INSTALL_DIR\logs\thor-error.log"
& $nssmPath set $SERVICE_NAME AppRotateFiles 1
& $nssmPath set $SERVICE_NAME AppRotateBytes 5000000
& $nssmPath set $SERVICE_NAME Start SERVICE_AUTO_START
& $nssmPath set $SERVICE_NAME ObjectName LocalSystem
New-Item -ItemType Directory -Force -Path "$INSTALL_DIR\logs" | Out-Null
& $nssmPath start $SERVICE_NAME
Write-OK "THOR service installed and started"

# ── Shortcuts ─────────────────────────────────────────────────────────────────
Write-Step "Creating shortcuts..."
$shell = New-Object -ComObject WScript.Shell

$startMenu = [Environment]::GetFolderPath("CommonPrograms")
$sc1 = $shell.CreateShortcut("$startMenu\THOR Dashboard.lnk")
$sc1.TargetPath = "http://localhost:$PORT"
$sc1.Description = "THOR Bitcoin Intelligence Dashboard"
$sc1.Save()

$desktop = [Environment]::GetFolderPath("CommonDesktopDirectory")
$sc2 = $shell.CreateShortcut("$desktop\THOR Dashboard.lnk")
$sc2.TargetPath = "http://localhost:$PORT"
$sc2.Description = "THOR Bitcoin Intelligence Dashboard"
$sc2.Save()
Write-OK "Shortcuts created (Start Menu + Desktop)"

# ── Open browser ──────────────────────────────────────────────────────────────
Write-Step "Opening THOR in your browser..."
Start-Sleep -Seconds 3
Start-Process "http://localhost:$PORT"

Write-Host @"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ✓ THOR installed successfully!

  Dashboard:  http://localhost:$PORT
  Config:     $envPath
  Logs:       $INSTALL_DIR\logs\

  THOR starts automatically with Windows.
  First load: create your password in the browser.
  Add Kraken API keys to .env for live trading.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"@ -ForegroundColor Green

Read-Host "`nPress Enter to close"
