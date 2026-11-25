; Compiled by scripts/build_windows_installer.py from the verified release ZIP.
; PAYLOAD_INCLUDE supplies only literal File/Delete/RMDir instructions.
Unicode true
RequestExecutionLevel user
ManifestDPIAware true
ManifestSupportedOS all
SetCompressor /SOLID lzma
SetCompressorDictSize 32
SetDatablockOptimize on
SetDateSave off
SetOverwrite on

!include "MUI2.nsh"
!include "nsDialogs.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "${PAYLOAD_INCLUDE}"

Name "HidroCAUMAX ${HCAU_VERSION}"
OutFile "${HCAU_OUTPUT}"
Icon "${HCAU_ICON}"
UninstallIcon "${HCAU_ICON}"
BrandingText "HidroCAUMAX · complemento para QGIS 3"
VIProductVersion "${HCAU_VERSION}.0"
VIAddVersionKey /LANG=1034 "ProductName" "HidroCAUMAX"
VIAddVersionKey /LANG=1034 "ProductVersion" "${HCAU_VERSION}"
VIAddVersionKey /LANG=1034 "FileVersion" "${HCAU_VERSION}"
VIAddVersionKey /LANG=1034 "FileDescription" "Instalador de HidroCAUMAX para un perfil QGIS 3"
VIAddVersionKey /LANG=1034 "LegalCopyright" "HidroCAUMAX · JM"

Var ProfilesRoot
Var ProfileName
Var ProfilePath
Var ProfileCombo
Var DestinationText
Var Dialog
Var FindHandle
Var FindName
Var OperationDir
Var StageDir
Var BackupDir
Var MovedPrevious
Var RegistryKey
Var Installed

!define MUI_ICON "${HCAU_ICON}"
!define MUI_UNICON "${HCAU_ICON}"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "HidroCAUMAX ${HCAU_VERSION}"
!define MUI_WELCOMEPAGE_TEXT "Este asistente instala HidroCAUMAX en un perfil de QGIS 3 del usuario actual.$\r$\n$\r$\nRequiere QGIS ${HCAU_QGIS_MIN}–${HCAU_QGIS_MAX} para Windows de 64 bits y el complemento CAUMAX original.$\r$\n$\r$\nCierra QGIS antes de continuar. No se necesitan permisos de administrador."
!insertmacro MUI_PAGE_WELCOME
Page custom ProfilePage ProfilePageLeave
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TITLE "HidroCAUMAX instalado"
!define MUI_FINISHPAGE_TEXT "Perfil: $ProfileName$\r$\n$\r$\nDestino: $INSTDIR$\r$\n$\r$\nAbre QGIS con este perfil y activa HidroCAUMAX en Complementos → Administrar e instalar complementos.$\r$\n$\r$\nSi había una instalación anterior, su copia se conserva en:$\r$\n$BackupDir"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH
!insertmacro MUI_LANGUAGE "Spanish"

Function .onInit
  SetShellVarContext current
  StrCpy $ProfilesRoot "$APPDATA\QGIS\QGIS3\profiles"
  StrCpy $ProfileName "default"
  StrCpy $MovedPrevious "0"
  StrCpy $Installed "0"
  StrCpy $OperationDir ""
  StrCpy $StageDir ""
  StrCpy $BackupDir "No existía una instalación anterior."
  ; A silent run would bypass the visible profile/destination review.
  IfSilent 0 +3
    SetErrorLevel 2
    Quit
FunctionEnd

Function UpdateDestination
  ${NSD_GetText} $ProfileCombo $ProfileName
  StrCpy $ProfilePath "$ProfilesRoot\$ProfileName"
  StrCpy $INSTDIR "$ProfilePath\python\plugins\caumax_consulta"
  ${NSD_SetText} $DestinationText $INSTDIR
FunctionEnd

Function ProfileChanged
  Pop $0
  Call UpdateDestination
FunctionEnd

Function ProfilePage
  !insertmacro MUI_HEADER_TEXT "Perfil de QGIS 3" "El complemento se instala únicamente en el perfil seleccionado."
  nsDialogs::Create 1018
  Pop $Dialog
  StrCmp $Dialog error 0 +2
    Abort
  ${NSD_CreateLabel} 0 0 100% 22u "Selecciona el perfil de QGIS 3 del usuario actual:"
  Pop $0
  ${NSD_CreateDropList} 0 25u 100% 80u ""
  Pop $ProfileCombo
  ${NSD_OnChange} $ProfileCombo ProfileChanged
  FindFirst $FindHandle $FindName "$ProfilesRoot\*"
  profile_loop:
    StrCmp $FindName "" profile_done
    StrCmp $FindName "." profile_next
    StrCmp $FindName ".." profile_next
    IfFileExists "$ProfilesRoot\$FindName\." 0 profile_next
      ${NSD_CB_AddString} $ProfileCombo $FindName
    profile_next:
      FindNext $FindHandle $FindName
      Goto profile_loop
  profile_done:
  FindClose $FindHandle
  SendMessage $ProfileCombo ${CB_GETCOUNT} 0 0 $0
  ${If} $0 == 0
    ${NSD_CB_AddString} $ProfileCombo "default"
  ${EndIf}
  SendMessage $ProfileCombo ${CB_SETCURSEL} 0 0
  ${NSD_CB_SelectString} $ProfileCombo $ProfileName
  ${NSD_CreateLabel} 0 52u 100% 12u "Destino de instalación:"
  Pop $0
  ${NSD_CreateMLText} 0 64u 100% 27u ""
  Pop $DestinationText
  SendMessage $DestinationText ${EM_SETREADONLY} 1 0
  ${NSD_CreateLabel} 0 96u 100% 33u "Otros perfiles, complementos y datos se conservan. Sin perfiles detectados se creará default. Para perfiles externos a esta ubicación, instala el ZIP oficial desde QGIS."
  Pop $0
  Call UpdateDestination
  nsDialogs::Show
FunctionEnd

Function ProfilePageLeave
  Call UpdateDestination
  StrCmp $ProfileName "" 0 +3
    MessageBox MB_OK|MB_ICONEXCLAMATION "Selecciona un perfil."
    Abort
  StrCpy $RegistryKey "Software\Microsoft\Windows\CurrentVersion\Uninstall\HidroCAUMAX-QGIS3-$ProfileName"
  IfFileExists "$INSTDIR\." 0 profile_accepted
  ReadINIStr $0 "$INSTDIR\metadata.txt" "general" "name"
  StrCpy $0 $0 11
  StrCmp $0 "HidroCAUMAX" profile_accepted
    MessageBox MB_OK|MB_ICONSTOP "El destino existe, pero no se reconoce como HidroCAUMAX. No se modificará:$\r$\n$INSTDIR"
    Abort
  profile_accepted:
FunctionEnd

Function CleanupStage
  StrCmp $StageDir "" cleanup_end
  !insertmacro HCAU_DELETE_PAYLOAD "$StageDir"
  !insertmacro HCAU_DELETE_METADATA "$StageDir"
  !insertmacro HCAU_REMOVE_EMPTY_DIRS "$StageDir"
  Delete "$StageDir\HidroCAUMAX_payload_manifest.json"
  Delete "$StageDir\HidroCAUMAX-install.ini"
  Delete "$StageDir\uninstall-HidroCAUMAX.exe"
  RMDir "$StageDir"
  StrCmp $OperationDir "" cleanup_end
  ; Non-recursive: a previous installation is never removed by cleanup.
  RMDir "$OperationDir"
  cleanup_end:
FunctionEnd

Function RestorePrevious
  StrCmp $MovedPrevious "1" 0 restore_end
  IfFileExists "$INSTDIR\." restore_end
  ClearErrors
  Rename "$BackupDir" "$INSTDIR"
  IfErrors restore_failed
  StrCpy $MovedPrevious "0"
  DetailPrint "Se ha restaurado la instalación anterior."
  Goto restore_end
  restore_failed:
  MessageBox MB_OK|MB_ICONSTOP "No se ha podido restaurar automáticamente la instalación anterior. Sus archivos están conservados en:$\r$\n$BackupDir$\r$\n$\r$\nDestino original: $INSTDIR"
  restore_end:
FunctionEnd

Function .onInstFailed
  Call RestorePrevious
  Call CleanupStage
FunctionEnd

Section "HidroCAUMAX" Main
  SectionIn RO
  ClearErrors
  CreateDirectory "$ProfilePath\python\plugins"
  CreateDirectory "$ProfilePath\python\.HidroCAUMAX-install"
  GetTempFileName $OperationDir "$ProfilePath\python\.HidroCAUMAX-install"
  IfErrors prepare_failed
  Delete "$OperationDir"
  CreateDirectory "$OperationDir"
  StrCpy $StageDir "$OperationDir\new"
  StrCpy $BackupDir "$OperationDir\previous"
  CreateDirectory "$StageDir"
  IfErrors prepare_failed
  !insertmacro HCAU_INSTALL_PAYLOAD "$StageDir"
  SetOutPath "$StageDir"
  File /oname=HidroCAUMAX_payload_manifest.json "${HCAU_PROOF}"
  WriteINIStr "$StageDir\HidroCAUMAX-install.ini" "install" "id" "caumax_consulta"
  WriteINIStr "$StageDir\HidroCAUMAX-install.ini" "install" "version" "${HCAU_VERSION}"
  WriteINIStr "$StageDir\HidroCAUMAX-install.ini" "install" "payload_sha256" "${HCAU_PAYLOAD_SHA256}"
  WriteINIStr "$StageDir\HidroCAUMAX-install.ini" "install" "target" "$INSTDIR"
  WriteINIStr "$StageDir\HidroCAUMAX-install.ini" "install" "profile_name" "$ProfileName"
  WriteUninstaller "$StageDir\uninstall-HidroCAUMAX.exe"
  IfErrors prepare_failed
  ; All new files have been written before the current plugin is touched.
  SetOutPath "$ProfilePath\python"
  IfFileExists "$INSTDIR\." 0 install_new
  ClearErrors
  Rename "$INSTDIR" "$BackupDir"
  IfErrors backup_failed
  StrCpy $MovedPrevious "1"
  install_new:
  ClearErrors
  Rename "$StageDir" "$INSTDIR"
  IfErrors activate_failed
  StrCpy $Installed "1"
  StrCpy $StageDir ""
  WriteRegStr HKCU "$RegistryKey" "DisplayName" "HidroCAUMAX ${HCAU_VERSION} · QGIS 3 · $ProfileName"
  WriteRegStr HKCU "$RegistryKey" "DisplayVersion" "${HCAU_VERSION}"
  WriteRegStr HKCU "$RegistryKey" "Publisher" "JM"
  WriteRegStr HKCU "$RegistryKey" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "$RegistryKey" "DisplayIcon" "$INSTDIR\uninstall-HidroCAUMAX.exe"
  WriteRegStr HKCU "$RegistryKey" "UninstallString" '$\"$INSTDIR\uninstall-HidroCAUMAX.exe$\"'
  WriteRegDWORD HKCU "$RegistryKey" "NoModify" 1
  WriteRegDWORD HKCU "$RegistryKey" "NoRepair" 1
  WriteRegDWORD HKCU "$RegistryKey" "EstimatedSize" ${HCAU_ESTIMATED_KB}
  ${If} $MovedPrevious == "1"
    DetailPrint "Copia anterior conservada: $BackupDir"
  ${Else}
    RMDir "$OperationDir"
    StrCpy $BackupDir "No existía una instalación anterior."
  ${EndIf}
  DetailPrint "HidroCAUMAX instalado en $INSTDIR"
  Goto install_end
  prepare_failed:
    Call CleanupStage
    MessageBox MB_OK|MB_ICONSTOP "No se han podido preparar los archivos. Comprueba permisos y espacio libre. La instalación anterior no se ha sustituido."
    SetErrorLevel 1
    Abort
  backup_failed:
    Call CleanupStage
    MessageBox MB_OK|MB_ICONSTOP "No se puede apartar la instalación anterior. Cierra QGIS y vuelve a intentarlo. Sus archivos se han conservado."
    SetErrorLevel 1
    Abort
  activate_failed:
    Call RestorePrevious
    Call CleanupStage
    MessageBox MB_OK|MB_ICONSTOP "No se ha podido activar la nueva instalación. Consulta los detalles de restauración y cierra QGIS antes de reintentarlo."
    SetErrorLevel 1
    Abort
  install_end:
SectionEnd

Function un.onInit
  SetShellVarContext current
  ReadINIStr $0 "$INSTDIR\HidroCAUMAX-install.ini" "install" "id"
  StrCmp $0 "caumax_consulta" 0 uninstall_refused
  ReadINIStr $0 "$INSTDIR\HidroCAUMAX-install.ini" "install" "payload_sha256"
  StrCmp $0 "${HCAU_PAYLOAD_SHA256}" 0 uninstall_refused
  ; A QGIS ZIP update may leave this older uninstaller behind.
  ReadINIStr $0 "$INSTDIR\metadata.txt" "general" "version"
  StrCmp $0 "${HCAU_VERSION}" 0 uninstall_refused
  ReadINIStr $0 "$INSTDIR\metadata.txt" "general" "name"
  StrCpy $0 $0 11
  StrCmp $0 "HidroCAUMAX" 0 uninstall_refused
  ReadINIStr $0 "$INSTDIR\HidroCAUMAX-install.ini" "install" "target"
  GetFullPathName $0 "$0"
  GetFullPathName $1 "$INSTDIR"
  StrCmp $0 $1 0 uninstall_refused
  ReadINIStr $ProfileName "$INSTDIR\HidroCAUMAX-install.ini" "install" "profile_name"
  StrCmp $ProfileName "" uninstall_refused
  StrCmp $ProfileName "." uninstall_refused
  StrCmp $ProfileName ".." uninstall_refused
  System::Call 'shlwapi::PathIsFileSpecW(w "$ProfileName") i.r0'
  StrCmp $0 "0" uninstall_refused
  GetFullPathName $0 "$APPDATA\QGIS\QGIS3\profiles\$ProfileName\python\plugins\caumax_consulta"
  StrCmp $0 $1 0 uninstall_refused
  StrCpy $RegistryKey "Software\Microsoft\Windows\CurrentVersion\Uninstall\HidroCAUMAX-QGIS3-$ProfileName"
  Return
  uninstall_refused:
  MessageBox MB_OK|MB_ICONSTOP "No se reconoce esta instalación, o el desinstalador corresponde a una copia anterior. No se eliminará ningún archivo."
  Abort
FunctionEnd

Section "Uninstall"
  SetOutPath "$TEMP"
  ClearErrors
  !insertmacro HCAU_DELETE_PAYLOAD "$INSTDIR"
  IfErrors uninstall_locked
  ; Keep the version guard until every program file has been removed, so a
  ; locked file leaves this same uninstaller usable after QGIS is closed.
  !insertmacro HCAU_DELETE_METADATA "$INSTDIR"
  IfErrors uninstall_locked
  !insertmacro HCAU_REMOVE_EMPTY_DIRS "$INSTDIR"
  Delete "$INSTDIR\HidroCAUMAX_payload_manifest.json"
  Delete "$INSTDIR\HidroCAUMAX-install.ini"
  Delete "$INSTDIR\uninstall-HidroCAUMAX.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "$RegistryKey"
  DetailPrint "Se han retirado los archivos propios. Se conservan datos, configuración, otros perfiles y copias anteriores."
  Goto uninstall_end
  uninstall_locked:
  MessageBox MB_OK|MB_ICONEXCLAMATION "Algunos archivos están en uso. Cierra QGIS y ejecuta de nuevo este desinstalador. Se conserva el registro para poder reintentarlo."
  SetErrorLevel 1
  Abort
  uninstall_end:
SectionEnd
