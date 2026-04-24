; ─────────────────────────────────────────────────────────────────────────────
; THOR Bitcoin Intelligence Dashboard — Inno Setup Script
; Builds THOR-Setup.exe — the Windows installer users download and run.
;
; Requirements:
;   - Inno Setup 6.x  (https://jrsoftware.org/isdl.php)
;   - Run: ISCC build_windows.iss
;
; Output: dist\THOR-Setup.exe
; ─────────────────────────────────────────────────────────────────────────────

#define AppName      "THOR Bitcoin Intelligence Dashboard"
#define AppShortName "THOR"
#define AppVersion   "1.0"
#define AppPublisher "RFBitcoin"
#define AppURL       "https://thor.rfbitcoin.com"
#define AppExeName   "THOR-Setup.exe"

[Setup]
AppId={{A1B2C3D4-THOR-RFBT-2026-E5F6A7B8C9D0}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
DefaultDirName=C:\THOR
DefaultGroupName={#AppShortName}
AllowNoIcons=yes
LicenseFile=LICENSE.txt
OutputDir=..\dist
OutputBaseFilename=THOR-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64 arm64

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; The PowerShell installer script — this does all the real work
Source: "install_windows.ps1"; DestDir: "{tmp}"; Flags: deleteafterinstall
; License file (shown in wizard — also installed for reference)
Source: "LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\THOR Dashboard"; Filename: "{app}\launch.bat"
Name: "{commondesktop}\THOR Dashboard"; Filename: "{app}\launch.bat"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"

[Run]
; Run the PowerShell installer after extraction
Filename: "powershell.exe"; \
  Parameters: "-ExecutionPolicy Bypass -NoProfile -WindowStyle Normal -File ""{tmp}\install_windows.ps1"""; \
  Flags: waituntilterminated; \
  StatusMsg: "Installing THOR (this may take a few minutes)..."

; Open browser when done
Filename: "{app}\launch.bat"; \
  Flags: nowait postinstall skipifsilent; \
  Description: "Open THOR Dashboard in browser"

[UninstallRun]
; Stop and remove the Windows service on uninstall
Filename: "powershell.exe"; \
  Parameters: "-Command ""& 'C:\THOR\tools\nssm.exe' stop THOR; & 'C:\THOR\tools\nssm.exe' remove THOR confirm"""; \
  RunOnceId: "RemoveTHORService"

