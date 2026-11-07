Option Explicit

Dim shell, files, projectRoot, command, argument
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
projectRoot = files.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = projectRoot
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " & Quoted(projectRoot & "\start.ps1")
For Each argument In WScript.Arguments
    command = command & " " & Quoted(argument)
Next
shell.Run command, 0, False

Function Quoted(value)
    Quoted = Chr(34) & Replace(CStr(value), Chr(34), Chr(34) & Chr(34)) & Chr(34)
End Function
