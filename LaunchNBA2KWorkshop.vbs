' NBA2K Workshop — double-click to launch (no console window).
' Requires Python venv at .\venv\Scripts\pythonw.exe and launcher.py in this folder.
Option Explicit
Dim fso, sh, root, pyw, script
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = root & "\venv\Scripts\pythonw.exe"
script = root & "\launcher.py"
If Not fso.FileExists(pyw) Then
  pyw = fso.GetAbsolutePathName(root & "\venv\Scripts\python.exe")
End If
If Not fso.FileExists(pyw) Or Not fso.FileExists(script) Then
  MsgBox "Could not find venv\Scripts\pythonw.exe and launcher.py in:" & vbCrLf & root, _
    vbExclamation, "NBA2K Workshop"
  WScript.Quit 1
End If
sh.CurrentDirectory = root
' 0 = hidden window, False = do not wait for exit
sh.Run """" & pyw & """ """ & script & """", 0, False
