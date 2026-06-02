; Vermes AI Agent - Inno Setup 安装包脚本
; 包含内嵌 Python 3.12 + 依赖 + 原生窗口

#define MyAppName "Vermes"
#define MyAppVersion "2.0.6"
#define MyAppPublisher "胜比特"
#define MyAppURL "https://vbit.top"
#define MyAppExeName "vermes-start.bat"

[Setup]
AppId={{B8E5C5A0-7F3D-4A2E-9C1B-1234567890AB}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=installer-output
OutputBaseFilename=Vermes-Setup-x64
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=packaging\vermes.ico
UninstallDisplayIcon={app}\packaging\vermes.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Portable Python (extract during install)
Source: "portable-python.zip"; DestDir: "{app}"; Flags: ignoreversion
; App files (hermes_cli, agent, tools, skills, etc.)
Source: "hermes_cli\*"; DestDir: "{app}\hermes_cli"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "agent\*"; DestDir: "{app}\agent"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "tools\*"; DestDir: "{app}\tools"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "skills\*"; DestDir: "{app}\skills"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "plugins\*"; DestDir: "{app}\plugins"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "gateway\*"; DestDir: "{app}\gateway"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "cron\*"; DestDir: "{app}\cron"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "locales\*"; DestDir: "{app}\locales"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "acp_adapter\*"; DestDir: "{app}\acp_adapter"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "acp_registry\*"; DestDir: "{app}\acp_registry"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "packaging\vermes.ico"; DestDir: "{app}\packaging"; Flags: ignoreversion
; Root Python files
Source: "run_agent.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "hermes_constants.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "model_tools.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "toolsets.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "toolset_distributions.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "utils.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "hermes_bootstrap.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "hermes_logging.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "hermes_state.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "hermes_time.py"; DestDir: "{app}"; Flags: ignoreversion
; Launcher
Source: "installer\vermes-start.bat"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\packaging\vermes.ico"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\packaging\vermes.ico"; Tasks: desktopicon

[Run]
; Extract portable Python
Filename: "{sys}\cmd.exe"; Parameters: "/c cd /d ""{app}"" && powershell -command ""Expand-Archive -Path portable-python.zip -DestinationPath python -Force"" && del portable-python.zip"; StatusMsg: "Extracting Python runtime..."; Flags: runhidden waituntilterminated
; Launch after install
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Vermes"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
Type: filesandordirs; Name: "{localappdata}\vermes"

[Code]
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  Exec('cmd.exe', '/c taskkill /F /FI "WINDOWTITLE eq Vermes*" 2>nul', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(500);
end;

function InitializeUninstall(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  Exec('cmd.exe', '/c taskkill /F /FI "WINDOWTITLE eq Vermes*" 2>nul', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(500);
end;
