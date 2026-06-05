; Vermes NSIS custom include
; Force close running Vermes before installation

!macro customInit
  ; Kill any running Vermes processes before install
  nsExec::ExecToLog 'taskkill /F /IM Vermes.exe /T 2>nul'
  nsExec::ExecToLog 'taskkill /F /IM vermes-backend.exe /T 2>nul'
  Sleep 1000
!macroend

!macro customInstall
  ; Post-install: nothing special needed
!macroend

!macro customUnInit
  ; Kill before uninstall
  nsExec::ExecToLog 'taskkill /F /IM Vermes.exe /T 2>nul'
  nsExec::ExecToLog 'taskkill /F /IM vermes-backend.exe /T 2>nul'
  Sleep 1000
!macroend
