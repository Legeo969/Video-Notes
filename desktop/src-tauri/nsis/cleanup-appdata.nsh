!macro NSIS_HOOK_POSTUNINSTALL
  RMDir /r "$LOCALAPPDATA\Video Notes AI\.jobs"
  RMDir /r "$LOCALAPPDATA\Video Notes AI\jobs"
  RMDir /r "$LOCALAPPDATA\Video Notes AI\state"
  RMDir /r "$LOCALAPPDATA\Video Notes AI\engine-runtime"
  RMDir /r "$LOCALAPPDATA\Video Notes AI\logs"
  RMDir "$LOCALAPPDATA\Video Notes AI"
!macroend
