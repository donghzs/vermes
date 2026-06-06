; Vermes NSIS custom include
; Force close running Vermes before installation

!macro customInit
  ; Kill any running Vermes processes before install (ignore errors)
  nsExec::ExecToStack 'cmd /c taskkill /F /IM Vermes.exe /T'
  Pop $0
  nsExec::ExecToStack 'cmd /c taskkill /F /IM vermes-backend.exe /T'
  Pop $0
  ; Wait briefly for processes to exit
  Sleep 500
!macroend

!macro customInstall
  ; Post-install: nothing special needed
!macroend

!macro customUnInit
  ; Kill before uninstall (ignore errors)
  nsExec::ExecToStack 'cmd /c taskkill /F /IM Vermes.exe /T'
  Pop $0
  nsExec::ExecToStack 'cmd /c taskkill /F /IM vermes-backend.exe /T'
  Pop $0
  Sleep 500
!macroend
