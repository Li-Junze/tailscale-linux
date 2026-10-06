' ============================================================
'  Tailscale Remote Toolbox - launcher (no console window)
'
'  Double-click to start the GUI. NO black console window appears.
'
'  This file only locates pythonw.exe and then calls launch.py,
'  which sits right next to it. Everything else (verify PyQt5,
'  report errors) lives in launch.py.
'
'  Why a .vbs and not a .bat? A .bat always opens a console window,
'  and closing that window with the mouse kills the GUI along with
'  it. VBScript + pythonw.exe gives a launch with zero windows.
'
'  IMPORTANT: keep this file ASCII-only. VBScript reads the source
'  using the system ANSI code page; non-ASCII bytes get mis-decoded.
' ============================================================

Option Explicit

Dim fso, sh, here, launch, pyw
Dim pythons, i, o, firstLine

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")

here = fso.GetParentFolderName(WScript.ScriptFullName)

' ---- 1. launch.py sits next to this file -----------------------------
launch = here & "\launch.py"
If Not fso.FileExists(launch) Then
    MsgBox "Cannot find launch.py next to this file." & vbCrLf & vbCrLf & _
           "Expected:" & vbCrLf & launch, _
           vbExclamation, "Tailscale Toolbox"
    WScript.Quit 1
End If

' ---- 2. locate pythonw.exe ------------------------------------------
pythons = Array( _
    "C:\Python314\pythonw.exe", _
    "C:\Python313\pythonw.exe", _
    "C:\Python312\pythonw.exe", _
    "C:\Python311\pythonw.exe", _
    "C:\Python310\pythonw.exe", _
    sh.ExpandEnvironmentStrings( _
        "%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe"), _
    sh.ExpandEnvironmentStrings( _
        "%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"), _
    sh.ExpandEnvironmentStrings( _
        "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"), _
    sh.ExpandEnvironmentStrings("%USERPROFILE%\anaconda3\pythonw.exe"), _
    sh.ExpandEnvironmentStrings("%USERPROFILE%\miniconda3\pythonw.exe"), _
    "pythonw.exe")

pyw = ""
For i = 0 To UBound(pythons)
    If Len(Trim(pythons(i))) > 0 Then
        If i = UBound(pythons) Then
            On Error Resume Next
            o = sh.Exec("where pythonw.exe").StdOut.ReadAll
            On Error Goto 0
            If Len(Trim(o)) > 0 Then
                firstLine = Split(Trim(o), vbCrLf)(0)
                If fso.FileExists(firstLine) Then pyw = firstLine
            End If
        ElseIf fso.FileExists(pythons(i)) Then
            pyw = pythons(i)
        End If
        If Len(pyw) > 0 Then Exit For
    End If
Next

If Len(pyw) = 0 Then
    MsgBox "Cannot find pythonw.exe (Python without console)." & vbCrLf & vbCrLf & _
           "Install Python first, then run:" & vbCrLf & _
           "    python -m pip install PyQt5", _
           vbCritical, "Tailscale Toolbox"
    WScript.Quit 1
End If

' ---- 3. hand over (window style 0 = completely hidden) ----------------
sh.Run """" & pyw & """ """ & launch & """", 0, False
