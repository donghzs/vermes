; Vermes NSIS custom include
; Force close running Vermes before installation.
;
; 注意：本文件必须放在 buildResources 目录（默认 build/），且根 package.json
; 的 nsis.include 指向 "installer.nsh" 才会被 electron-builder 编译进安装器。
; 之前 electron/installer.nsh 从未生效（Windows 构建用根 package.json，缺 include）。
;
; 进程名核对（2026-09-18）：
;   - Electron 壳主进程   = Vermes.exe
;   - 后端 PyInstaller 产物 = vermes.exe（spec name='vermes'，旧写 vermes-backend.exe 是错的）
;   - 渠道网关 gateway      = vermes.exe -m vermes_cli.main gateway run（同一 exe）
; 故只杀 Vermes.exe + vermes.exe 两个名字即可覆盖全部。

!macro customInit
  ; Kill any running Vermes processes before install (ignore errors)
  nsExec::ExecToStack 'cmd /c taskkill /F /IM Vermes.exe /T'
  Pop $0
  nsExec::ExecToStack 'cmd /c taskkill /F /IM vermes.exe /T'
  Pop $0
  ; Wait briefly for processes to exit
  Sleep 800
!macroend

!macro customInstall
  ; Post-install: nothing special needed
!macroend

!macro customUnInit
  ; Kill before uninstall (ignore errors)
  nsExec::ExecToStack 'cmd /c taskkill /F /IM Vermes.exe /T'
  Pop $0
  nsExec::ExecToStack 'cmd /c taskkill /F /IM vermes.exe /T'
  Pop $0
  Sleep 800
!macroend
