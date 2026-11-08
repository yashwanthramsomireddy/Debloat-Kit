; ============================================================
;  DebloatKit v2.0 -- Inno Setup Installer Script
;  TeamExyKings | Yashwanth Ram Somireddy | Chennai, India
;  MIT License
;
;  Prerequisites:
;    - Inno Setup 6.x  ->  https://jrsoftware.org/isinfo.php
;    - Run build.bat first to generate dist\DebloatKit.exe
;
;  To build:
;    Double-click build_installer.bat
;    OR open this .iss in Inno Setup Compiler and press F9
;    Output: installer_output\DebloatKit_Setup_v2.0.exe
; ============================================================

#define MyAppName      "DebloatKit"
#define MyAppVersion   "2.0"
#define MyAppPublisher "TeamExyKings"
#define MyAppAuthor    "Yashwanth Ram Somireddy"
#define MyAppURL       "https://github.com/yashwanthramsomireddy/DebloatKit"
#define MyAppExeName   "DebloatKit.exe"
#define MyAppYear      "2026"

[Setup]
AppId={{A3F8B2C1-4D7E-4A9F-8B3C-2E5F1D6A8B9C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
AppCopyright=Copyright (C) {#MyAppYear} {#MyAppAuthor}

; Install to Program Files (x86)
DefaultDirName={autopf32}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=installer_output
OutputBaseFilename=DebloatKit_Setup_v{#MyAppVersion}

; Use our generated icon
SetupIconFile=assets\icon.ico

; Installer wizard bitmaps (from assets folder in project root)
WizardStyle=modern
WizardSmallImageFile=assets\installer_icon_55x58.bmp
WizardImageFile=assets\installer_side_164x314.bmp

; Compression
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes

; *** FIX: Do NOT request elevation here -- the exe handles UAC itself ***
; PrivilegesRequired=admin causes the 740 error on some systems.
; PyInstaller --uac-admin already embeds the UAC manifest in the exe.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; Misc
DisableProgramGroupPage=yes
DisableWelcomePage=no
AllowNoIcons=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} v{#MyAppVersion}
VersionInfoVersion={#MyAppVersion}.0.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} -- Samsung Galaxy Debloater for Windows
VersionInfoCopyright=Copyright (C) {#MyAppYear} {#MyAppAuthor}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
MinVersion=10.0.17763

; License agreement shown before install
LicenseFile=LICENSE.rtf

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "startupicon"; Description: "Launch DebloatKit on &Windows startup"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
; Main executable
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

; Package database
Source: "data\packages.json"; DestDir: "{app}\data"; Flags: ignoreversion

; App icon (for shortcuts)
Source: "assets\icon.ico"; DestDir: "{app}\assets"; Flags: ignoreversion

; Docs
Source: "README.md";    DestDir: "{app}"; Flags: ignoreversion
Source: "CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE";      DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE.rtf";  DestDir: "{app}"; Flags: ignoreversion

[Dirs]
; Create backups folder inside the install dir
Name: "{app}\backups"

[Icons]
Name: "{group}\{#MyAppName}";            Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\icon.ico"
Name: "{group}\Uninstall {#MyAppName}";  Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}";      Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\icon.ico"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}";      Filename: "{app}\{#MyAppExeName}"; Tasks: startupicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\backups"
Type: filesandordirs; Name: "{app}\data"

[Code]
// -- ADB check after install --------------------------------------------------
function ADBAvailable(): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec('cmd.exe', '/c adb version >nul 2>&1', '', SW_HIDE,
                 ewWaitUntilTerminated, ResultCode)
            and (ResultCode = 0);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if not ADBAvailable() then
      MsgBox(
        'ADB (Android Debug Bridge) was not found on your system.' + #13#10 + #13#10 +
        'DebloatKit requires ADB to communicate with your Galaxy device.' + #13#10 + #13#10 +
        'Download ADB Platform Tools:' + #13#10 +
        'https://dl.google.com/android/repository/platform-tools-latest-windows.zip' + #13#10 + #13#10 +
        'Extract the ZIP and either:' + #13#10 +
        '  1. Add the folder to your Windows PATH, or' + #13#10 +
        '  2. Set the ADB path in DebloatKit -> Settings' + #13#10 + #13#10 +
        'You can still open DebloatKit and set the path in Settings.',
        mbInformation, MB_OK);
  end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := MsgBox(
    'Are you sure you want to uninstall {#MyAppName} v{#MyAppVersion}?',
    mbConfirmation, MB_YESNO
  ) = IDYES;
end;
