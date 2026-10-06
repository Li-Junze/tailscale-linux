' ============================================================
'  启动工具箱.vbs -- 静默启动器 (背后不出现黑色控制台窗口)
'
'  为什么用 .vbs 而不是 .bat:
'    .bat 必然带一个 cmd 控制台窗口。GUI 起来之后它就杵在背后,
'    而且用命令行的方式"关掉那个黑窗口"会连带把 GUI 一起杀掉 ——
'    这正是之前"发个文件程序就退出"的根源之一。
'    pythonw.exe 本身不attach 控制台, 用它启动就一个黑框都没有。
'
'  这个脚本干的事:
'    1. 自检项目文件在不在
'    2. 挨个找带图形界面支持的 pythonw.exe
'    3. 用 Run(..., 0) 以"完全不显示窗口"的方式启动 GUI
'    4. 放一个哨兵文件, 4 秒后还在就说明启动失败 -> 写日志 + 弹窗
'
'  编码: GBK (与同目录的 .bat 一致)。换行: CRLF。
' ============================================================

Option Explicit

Dim fso, sh, here, pyw, logf, rc, i, o, firstLine, sentinel
Dim cands(10)

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")

here = fso.GetParentFolderName(WScript.ScriptFullName)
logf = here & "\启动失败.log"

' ---- 1. 项目自检 ----------------------------------------------------
If Not fso.FileExists(here & "\builder\build_gui.py") Then
    MsgBox "找不到项目文件:" & vbCrLf & vbCrLf & _
           here & "\builder\build_gui.py" & vbCrLf & vbCrLf & _
           "请确认本文件位于 tailscale-remote 文件夹的 builder 子目录里。", _
           vbExclamation, "Tailscale 工具箱"
    WScript.Quit 1
End If

' ---- 2. 挨个找 pythonw.exe -----------------------------------------
cands(0) = "C:\Python314\pythonw.exe"
cands(1) = "C:\Python313\pythonw.exe"
cands(2) = "C:\Python312\pythonw.exe"
cands(3) = "C:\Python311\pythonw.exe"
cands(4) = "C:\Python310\pythonw.exe"
cands(5) = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe")
cands(6) = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe")
cands(7) = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe")
cands(8) = sh.ExpandEnvironmentStrings("%USERPROFILE%\anaconda3\pythonw.exe")
cands(9) = sh.ExpandEnvironmentStrings("%USERPROFILE%\miniconda3\pythonw.exe")
cands(10) = "pythonw.exe"

pyw = ""
For i = 0 To UBound(cands)
    If Len(Trim(cands(i))) > 0 Then
        If i = 10 Then
            ' PATH 里的: 用 where 拿绝对路径
            On Error Resume Next
            o = sh.Exec("where pythonw.exe").StdOut.ReadAll
            On Error Goto 0
            If Len(Trim(o)) > 0 Then
                firstLine = Split(Trim(o), vbCrLf)(0)
                If fso.FileExists(firstLine) Then pyw = firstLine
            End If
        ElseIf fso.FileExists(cands(i)) Then
            pyw = cands(i)
        End If
        If Len(pyw) > 0 Then Exit For
    End If
Next

If Len(pyw) = 0 Then
    Call ShowErr("找不到能运行图形界面的 Python。", _
                 "请安装 Python, 然后执行:" & vbCrLf & _
                 "    python -m pip install PyQt5")
    WScript.Quit 1
End If

' ---- 3. 放哨兵 + 静默启动 ------------------------------------------
'   build_gui.py 正常起来后会删掉这个哨兵文件。
sentinel = here & "\.toolbox-alive"
If fso.FileExists(sentinel) Then fso.DeleteFile sentinel
fso.CreateTextFile(sentinel).Close

'   窗口样式: 0 = 完全不显示窗口(无黑框)
rc = sh.Run("""" & pyw & """ """ & here & "\builder\build_gui.py""", 0, False)

WScript.Sleep 4000
If fso.FileExists(sentinel) Then
    ' 4 秒后哨兵还在 -> GUI 没起来
    Call WriteLog("启动失败", pyw)
    Call ShowErr("图形界面没能启动。", _
                 "解释器: " & pyw & vbCrLf & vbCrLf & _
                 "它可能没装 PyQt5。请执行:" & vbCrLf & _
                 "    " & pyw & " -m pip install PyQt5")
    WScript.Quit 1
End If

WScript.Quit 0


' ---- 辅助: 写日志 ---------------------------------------------------
Sub WriteLog(what, pyw)
    Dim f
    On Error Resume Next
    Set f = fso.CreateTextFile(logf, True)
    f.WriteLine "==== " & what & " ===="
    f.WriteLine "时间: " & Now()
    f.WriteLine "解释器: " & pyw
    f.WriteLine ""
    f.WriteLine "排查步骤:"
    f.WriteLine "1) 上面那个解释器没装 PyQt5, 执行:"
    f.WriteLine "     " & pyw & " -m pip install PyQt5"
    f.WriteLine "2) 若已装仍失败, 看 builder\debug.log 里的报错"
    f.WriteLine "3) 也可双击 ★运行生成器.bat 走带控制台的模式, 能看到报错"
    f.Close
    On Error Goto 0
End Sub


' ---- 辅助: 弹窗 ----------------------------------------------------
Sub ShowErr(head, body)
    MsgBox head & vbCrLf & vbCrLf & body & vbCrLf & vbCrLf & _
           "细节: " & logf, vbCritical, "Tailscale 工具箱"
End Sub
