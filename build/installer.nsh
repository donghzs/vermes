; Vermes NSIS custom include
; 强制关闭正在运行的 Vermes，避免覆盖安装/卸载弹出「Vermes 无法关闭」死循环。
;
; 注意：本文件必须放在 buildResources 目录（默认 build/），且根 package.json
; 的 nsis.include 指向 "installer.nsh" 才会被 electron-builder 编译进安装器。
; electron-builder 会把本文件作为 shared header 注入到「安装器」与「卸载器」两侧
; （NsisTarget.computeCommonInstallerScriptHeader），故安装与卸载两个阶段都生效。
;
; 进程名核对（2026-09-18）：
;   - Electron 壳主进程    = Vermes.exe
;   - 后端 PyInstaller 产物 = vermes.exe（spec name='vermes'，旧写 vermes-backend.exe 是错的）
;   - 渠道网关 gateway       = vermes.exe -m vermes_cli.main gateway run（同一 exe）
; 故按镜像名杀 Vermes.exe + vermes.exe 两个名字即可覆盖全部（taskkill 大小写不敏感）。

; ─────────────────────────────────────────────────────────────────────────────
; 根治：覆盖 customCheckAppRunning
;
; electron-builder 默认的 `_CHECK_APP_RUNNING`（allowOnlyOneInstallerInstance.nsh）
; 存在缺陷：
;   1) 它用 NSIS 插件 GetProcessInfo 读取「本体进程」信息（GetProcessInfo 0 → 当前进程），
;      而安装器/卸载器自身进程名恰好也叫 "Vermes Setup 2.4.9.exe"，与期望的
;      APP_EXECUTABLE_FILENAME="Vermes.exe" 不匹配 → 提前跳过整个检测/kill 流程；
;   2) 即便进入，FIND_PROCESS/KILL_PROCESS 走 PowerShell Get-CimInstance 按 $INSTDIR
;      前缀匹配，对「Path 为空」「刚被清理」的进程有盲区，容易连续两轮仍判定存活，
;      最终弹 appCannotBeClosed（「Vermes 无法关闭…请手动关闭后重试」）死循环。
;
; 覆盖面：CHECK_APP_RUNNING 在 installer（installSection.nsh）
;         与 uninstaller（un.checkAppRunning）两处都会 `!ifmacrodef customCheckAppRunning`
;         优先插入本宏，因此安装与卸载两个阶段都绕开默认缺陷逻辑。
; 策略：不做「先探测后杀」的判定，直接按镜像名强杀（幂等，无进程时 taskkill 返回非 0 但无害），
;       且绝不弹任何 MessageBox。
; ─────────────────────────────────────────────────────────────────────────────
!macro customCheckAppRunning
  nsExec::ExecToStack 'cmd /c taskkill /F /IM "Vermes.exe" /T'
  Pop $0
  nsExec::ExecToStack 'cmd /c taskkill /F /IM "vermes.exe" /T'
  Pop $0
  ; 给被终止进程一点时间释放文件句柄，避免 "files in use"
  Sleep 800
!macroend

!macro customInit
  ; 安装器 .onInit 阶段先杀一轮（双保险）
  nsExec::ExecToStack 'cmd /c taskkill /F /IM "Vermes.exe" /T'
  Pop $0
  nsExec::ExecToStack 'cmd /c taskkill /F /IM "vermes.exe" /T'
  Pop $0
  Sleep 800
!macroend

!macro customUnInit
  ; 卸载器在 un.onInit 阶段先杀一轮（双保险）
  nsExec::ExecToStack 'cmd /c taskkill /F /IM "Vermes.exe" /T'
  Pop $0
  nsExec::ExecToStack 'cmd /c taskkill /F /IM "vermes.exe" /T'
  Pop $0
  Sleep 800
!macroend

!macro customInstall
  ; 安装完成后无需额外动作
!macroend
