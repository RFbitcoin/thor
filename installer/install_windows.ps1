# ─────────────────────────────────────────────────────────────────────────────
# THOR Bitcoin Intelligence Dashboard — Windows Installer
# Wrapped into THOR-Setup.exe by build_windows.iss (Inno Setup)
# ─────────────────────────────────────────────────────────────────────────────
#Requires -Version 5.1

$ErrorActionPreference = "Stop"

$DIST_URL   = "https://thor.rfbitcoin.com/dist/thor-latest.zip"
$NSSM_URL   = "https://nssm.cc/release/nssm-2.24.zip"
$INSTALL_DIR = "C:\THOR"
$SERVICE_NAME = "THOR"
$PORT = 5000

function Write-Step { param($msg) Write-Host "`n▶ $msg" -ForegroundColor Yellow }
function Write-OK   { param($msg) Write-Host "  ✓ $msg" -ForegroundColor Green }
function Write-Fail { param($msg) Write-Host "  ✗ $msg" -ForegroundColor Red; exit 1 }

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
Write-Step "Checking Python 3.11+..."
$python = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd -c "import sys; print(sys.version_info >= (3,11))" 2>$null
        if ($ver -eq "True") { $python = $cmd; break }
    } catch {}
}

if (-not $python) {
    Write-Step "Installing Python 3.11 via winget..."
    try {
        winget install --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
        $python = "python"
        Write-OK "Python 3.11 installed"
    } catch {
        Write-Host ""
        Write-Host "  Could not auto-install Python." -ForegroundColor Red
        Write-Host "  Please install Python 3.11 from https://python.org/downloads/" -ForegroundColor Cyan
        Write-Host "  Then re-run this installer." -ForegroundColor Cyan
        Read-Host "`nPress Enter to exit"
        exit 1
    }
}
$pyver = & $python --version
Write-OK "Python: $pyver"

# ── Create install directory ──────────────────────────────────────────────────
Write-Step "Creating install directory..."
if (Test-Path $INSTALL_DIR) {
    # Stop existing service before overwriting
    try { Stop-Service $SERVICE_NAME -ErrorAction SilentlyContinue } catch {}
}
New-Item -ItemType Directory -Force -Path $INSTALL_DIR | Out-Null
Write-OK "Directory: $INSTALL_DIR"

# ── Download THOR ─────────────────────────────────────────────────────────────
Write-Step "Downloading THOR..."
$zipPath = "$env:TEMP\thor-latest.zip"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest -Uri $DIST_URL -OutFile $zipPath -UseBasicParsing
Write-OK "Downloaded"

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
Write-Step "Setting up Python environment..."
& $python -m venv "$INSTALL_DIR\venv"
& "$INSTALL_DIR\venv\Scripts\pip.exe" install --quiet --upgrade pip
& "$INSTALL_DIR\venv\Scripts\pip.exe" install --quiet -r "$INSTALL_DIR\requirements.txt"
Write-OK "Dependencies installed"

# ── NSSM (service manager) ────────────────────────────────────────────────────
Write-Step "Installing Windows service..."
$nssmPath = "$INSTALL_DIR\tools\nssm.exe"
if (-not (Test-Path $nssmPath)) {
    $nssmZip = "$env:TEMP\nssm.zip"
    Invoke-WebRequest -Uri $NSSM_URL -OutFile $nssmZip -UseBasicParsing
    $nssmExtract = "$env:TEMP\nssm_extract"
    Expand-Archive -LiteralPath $nssmZip -DestinationPath $nssmExtract -Force

    New-Item -ItemType Directory -Force -Path "$INSTALL_DIR\tools" | Out-Null
    # Pick correct architecture
    $arch = if ([Environment]::Is64BitOperatingSystem) { "win64" } else { "win32" }
    Copy-Item "$nssmExtract\nssm-2.24\$arch\nssm.exe" $nssmPath -Force
    Remove-Item $nssmZip, $nssmExtract -Recurse -Force
}

# Remove old service if exists
& $nssmPath stop $SERVICE_NAME 2>$null
& $nssmPath remove $SERVICE_NAME confirm 2>$null

# Register new service
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

# ── Start Menu shortcut ───────────────────────────────────────────────────────
Write-Step "Creating shortcuts..."
$startMenu = [Environment]::GetFolderPath("CommonPrograms")
$shortcutPath = "$startMenu\THOR Dashboard.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "http://localhost:$PORT"
$shortcut.Description = "THOR Bitcoin Intelligence Dashboard"
$shortcut.Save()

# Desktop shortcut
$desktop = [Environment]::GetFolderPath("CommonDesktopDirectory")
$dShortcut = $shell.CreateShortcut("$desktop\THOR Dashboard.lnk")
$dShortcut.TargetPath = "http://localhost:$PORT"
$dShortcut.Description = "THOR Bitcoin Intelligence Dashboard"
$dShortcut.Save()
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
