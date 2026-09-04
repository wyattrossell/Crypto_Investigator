; Inno Setup script for Crypto Investigator.
;
; Build with:  python tools/build_release.py   (runs PyInstaller, then this)
; Requires Inno Setup 6 (free): https://jrsoftware.org/isinfo.php
;
; Installs the PyInstaller output folder (dist\CryptoInvestigator) and
; creates Start-menu / optional desktop shortcuts. The installer works
; WITHOUT administrator rights (per-user install under %LOCALAPPDATA%\
; Programs) and offers an all-users install when run elevated.
;
; Case data is never touched: the program keeps its database, reports and
; logs under %LOCALAPPDATA%\CryptoInvestigator\data, which upgrades and
; uninstalls leave in place (evidence is not something an uninstaller
; should delete).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Crypto Investigator"
#define AppExe "CryptoInvestigator.exe"
#define AppPublisher "Crypto Investigator project"
#define AppURL "https://github.com/wyattrossell/Crypto_Investigator"

[Setup]
AppId={{7E1C1B6A-3C0E-4F5B-9B2A-6D0F2C8E41A7}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=CryptoInvestigator-Setup-v{#AppVersion}
SetupIconFile=..\assets\crypto_investigator.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; The running program holds this mutex (see app/launcher.py), so Setup can
; ask the user to close it before upgrading files in use.
AppMutex=CryptoInvestigatorRunning
CloseApplications=yes
RestartApplications=no
#ifexist "..\LICENSE"
LicenseFile=..\LICENSE
#endif
InfoBeforeFile=..\packaging\install-notes.txt

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\CryptoInvestigator\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Trace cryptocurrency and prepare legal process"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only the program folder. The data folder under %LOCALAPPDATA% is kept.
Type: filesandordirs; Name: "{app}\_internal"
