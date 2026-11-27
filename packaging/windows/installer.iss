; Inno Setup script for the Windows installer.
;
; Build the PyInstaller bundle first (dist\Raw MRI Data Explorer\), then from
; the repository root:
;   ISCC.exe /DAppVersion=0.2.0 packaging\windows\installer.iss
; Output: dist\RawMRIDataExplorer-<version>-windows-x64-setup.exe
;
; Installs per-user by default (no administrator prompt); the wizard offers an
; all-users install. The app is added to Explorer's "Open with" list for .dat
; files without taking over the default handler, because .dat is shared by
; many unrelated programs.

#ifndef AppVersion
  #error Pass the release version, e.g. ISCC /DAppVersion=0.2.0 installer.iss
#endif

#define AppName "Raw MRI Data Explorer"
#define AppExe "Raw MRI Data Explorer.exe"
#define AppPublisher "Farivar Lab"
#define AppURL "https://github.com/farivarlab/RAWDATA_Viewer"
#define Root "..\.."

[Setup]
; AppId identifies the product for upgrades/uninstall; never change it.
AppId={{89466E5F-D740-4F57-BA64-739EAB3B05FF}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile={#Root}\LICENSE
SetupIconFile={#Root}\siemens_explorer\resources\app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
OutputDir={#Root}\dist
OutputBaseFilename=RawMRIDataExplorer-{#AppVersion}-windows-x64-setup
; Let upgrades replace files of a running copy after the user closes it.
CloseApplications=yes
RestartApplications=no
ChangesAssociations=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "openwith"; Description: "Add to the ""Open with"" menu for .dat files"; GroupDescription: "File types:"

[Files]
Source: "{#Root}\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Remove the previous version's bundled libraries so upgrades never mix them.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; HKA = HKCU for per-user installs, HKLM for all-users installs.
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"; Flags: uninsdeletekey; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".dat"; ValueData: ""; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"",0"; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\.dat\OpenWithList\{#AppExe}"; ValueType: none; Flags: uninsdeletekey; Tasks: openwith

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
