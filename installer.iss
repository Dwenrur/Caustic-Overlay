; Inno Setup script for Caustic Overlay. build_installer.py compiles this; you don't need to.
; Installs for the current user only (no admin prompt) into %LOCALAPPDATA%\Programs\Caustic Overlay.
; Settings, the log and your own sounds live in %APPDATA%\Caustic Overlay and are kept on
; update and uninstall.
; Also cleans up the old "MultiChat Overlay" install (same app, old name) if it's there.

#ifndef AppVersion
  #define AppVersion "17"
#endif
#define AppName "Caustic Overlay"
#define AppExe "Caustic Overlay.exe"
; The old name's AppId and program, used only to remove the old install.
#define OldAppId "{8F3C2B7E-5A41-4D8B-9C6E-2E7A1F0B4D93}"
#define OldAppExe "MultiChat Overlay.exe"

[Setup]
; AppId identifies this app across updates. Never change it, or updates install side by side.
AppId={{3C9E5A1D-7B2F-4E86-A4D0-6F1B8C2E9D57}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} v{#AppVersion}
AppPublisher=Izzy
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=.
OutputBaseFilename=Caustic-Overlay-Setup-v{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; If the overlay is running during an update/uninstall, offer to close it.
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startup"; Description: "Start Caustic Overlay when &Windows starts"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: ".build\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Carry over the settings and custom sounds from the folder you built in (only if there are none yet).
; (If the old MultiChat Overlay was installed, the app itself copies its settings on first start.)
Source: ".build\seed\settings.json"; DestDir: "{userappdata}\{#AppName}"; Flags: onlyifdoesntexist skipifsourcedoesntexist uninsneveruninstall
Source: ".build\seed\sounds\*"; DestDir: "{userappdata}\{#AppName}\sounds"; Flags: onlyifdoesntexist skipifsourcedoesntexist recursesubdirs createallsubdirs uninsneveruninstall

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName} now"; Flags: nowait postinstall skipifsilent

[Code]
const
  OldUninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#OldAppId}_is1';

{ Close a running copy of the old MultiChat Overlay politely (it saves its settings on close),
  then force it if it's still there. }
procedure CloseOldApp();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM "{#OldAppExe}"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(1500);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM "{#OldAppExe}"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

{ Silently run the old MultiChat Overlay uninstaller: removes its program files, shortcuts and
  Apps-list entry. Its settings stay in %APPDATA%\MultiChat Overlay for the new app to copy. }
procedure RemoveOldInstall();
var
  Uninstaller: String;
  ResultCode: Integer;
  Waited: Integer;
begin
  if not RegQueryStringValue(HKCU, OldUninstallKey, 'UninstallString', Uninstaller) then
    exit;
  Uninstaller := RemoveQuotes(Uninstaller);
  if not FileExists(Uninstaller) then
    exit;
  Log('Removing old MultiChat Overlay install: ' + Uninstaller);
  CloseOldApp();
  Exec(Uninstaller, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Log('Old uninstaller exit code: ' + IntToStr(ResultCode));
  { Inno uninstallers hand off to a temporary copy of themselves, so also wait (up to 20 s)
    until its Apps-list entry is gone. }
  Waited := 0;
  while RegKeyExists(HKCU, OldUninstallKey) and (Waited < 40) do
  begin
    Sleep(500);
    Waited := Waited + 1;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    RemoveOldInstall();
end;
