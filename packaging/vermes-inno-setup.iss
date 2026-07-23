; Vermes AI Agent - Inno Setup 安装包脚本
; 用法: 在 A11 上用 Inno Setup Compiler 打开此文件编译
; 输出: Vermes-Setup-x64.exe (~90MB)
;
; 前置条件: PyInstaller COLLECT 构建已完成 (dist/Vermes/ 目录)

#define MyAppName "Vermes"
#define MyAppVersion "2.3.5"
#define MyAppPublisher "胜比特"
#define MyAppURL "https://vbit.top"
#define MyAppExeName "Vermes.exe"

[Setup]
AppId={{B8E5C5A0-7F3D-4A2E-9C1B-1234567890AB}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
LicenseFile=
OutputDir=installer-output
OutputBaseFilename=Vermes-Setup-x64
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 安装包图标（vermes.ico 已存在于 packaging/ 目录）
SetupIconFile=vermes.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
; 外观（bmp 资源未入仓库，用 Inno 默认向导图）
; WizardImageFile=vermes-wizard.bmp
; WizardSmallImageFile=vermes-icon-small.bmp

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "quicklaunchicon"; Description: "{cm:CreateQuickLaunchIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; PyInstaller COLLECT 输出的整个目录
Source: "dist\Vermes\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: quicklaunchicon

[Run]
; 安装完成后可选启动
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
; 清理用户数据（可选，询问用户）
Type: filesandordirs; Name: "{localappdata}\vermes"

[Registry]
; 注册 vermes:// 协议（可选，用于深度链接）
Root: HKA; Subkey: "Software\Classes\vermes"; ValueType: string; ValueName: ""; ValueData: "URL:Vermes Protocol"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\vermes"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\vermes\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

[Code]
// 安装前检查是否正在运行
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  // 安装前结束正在运行的 Vermes（不依赖 {app}，直接杀进程）
  Exec('taskkill', '/F /IM Vermes.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(500);
end;

// 卸载前检查是否正在运行
function InitializeUninstall(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  Exec('taskkill', '/F /IM Vermes.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(500);
end;
