Attribute VB_Name = "modNewStar"
Option Explicit

' New Star rubber factory - Excel edition, phase 1.
' Every Arabic text (captions, messages, statuses) lives in the hidden LISTS sheet (tblMsg),
' so this module stays ASCII and works on any Windows language setting.

#If VBA7 Then
    Private Declare PtrSafe Function MessageBoxW Lib "user32" (ByVal hWnd As LongPtr, ByVal lpText As LongPtr, _
        ByVal lpCaption As LongPtr, ByVal uType As Long) As Long
#Else
    Private Declare Function MessageBoxW Lib "user32" (ByVal hWnd As Long, ByVal lpText As Long, _
        ByVal lpCaption As Long, ByVal uType As Long) As Long
#End If

Public Const PWD As String = "NS#2026!"

Public Const MB_YESNO As Long = 4
Public Const MB_ICONERROR As Long = 16
Public Const MB_ICONQUESTION As Long = 32
Public Const MB_ICONINFO As Long = 64
Private Const MB_RTL As Long = &H180000

' Journal register (tblJE) columns
Public Const JE_NO As Long = 1, JE_DATE As Long = 2, JE_DESC As Long = 3, JE_CODE As Long = 4
Public Const JE_ACC As Long = 5, JE_PARTY As Long = 6, JE_DR As Long = 7, JE_CR As Long = 8
Public Const JE_NOTE As Long = 9, JE_STATUS As Long = 10, JE_BY As Long = 11, JE_AT As Long = 12
Public Const JE_APPBY As Long = 13, JE_APPAT As Long = 14, JE_APPNOTE As Long = 15
Public Const JE_COLS As Long = 15

' Approvals sheet layout
Private Const AP_FIRST As Long = 6, AP_LAST As Long = 205

Public CurrentUser As String
Public CurrentRole As String
Public gEvents As clsWbEvents

' ============================================================ startup

Public Sub Auto_Open()
    EnsureEvents
    LockDown
End Sub

Public Sub EnsureEvents()
    If gEvents Is Nothing Then
        Set gEvents = New clsWbEvents
        Set gEvents.wb = ThisWorkbook
    End If
End Sub

Public Function RequireLogin() As Boolean
    EnsureEvents
    If CurrentUser = "" Then
        LockDown
        MsgW Msg("ERR_SESSION"), MB_ICONINFO
        Exit Function
    End If
    RequireLogin = True
End Function

Public Sub BeginWork()
    Application.ScreenUpdating = False
    Application.EnableEvents = False
End Sub

Public Sub EndWork()
    Application.EnableEvents = True
    Application.ScreenUpdating = True
End Sub

' ============================================================ helpers

Public Function MsgW(ByVal text As String, Optional ByVal flags As Long = MB_ICONINFO) As Long
    Dim cap As String
    cap = Msg("APP_TITLE")
    MsgW = MessageBoxW(Application.hWnd, StrPtr(text), StrPtr(cap), flags Or MB_RTL)
End Function

Public Function Ask(ByVal text As String) As Boolean
    Ask = (MsgW(text, MB_YESNO Or MB_ICONQUESTION) = 6)
End Function

Public Sub Fail(ByVal key As String, Optional ByVal extra As String = "")
    MsgW Msg(key) & extra, MB_ICONERROR
End Sub

Public Sub NoPerm()
    Fail "ERR_NO_PERM"
End Sub

Public Function SafeText(ByVal v As Variant) As String
    If IsError(v) Then Exit Function
    If IsEmpty(v) Or IsNull(v) Then Exit Function
    SafeText = CStr(v)
End Function

Public Function NumVal(ByVal v As Variant) As Double
    If IsError(v) Or IsEmpty(v) Or IsNull(v) Then Exit Function
    If VarType(v) = vbString Then
        If Len(Trim$(v)) = 0 Then Exit Function
    End If
    If IsNumeric(v) Then NumVal = CDbl(v)
End Function

Public Function Arr2D(ByVal rng As Range) As Variant
    Dim a() As Variant
    If rng.Cells.CountLarge = 1 Then
        ReDim a(1 To 1, 1 To 1)
        a(1, 1) = rng.Value
        Arr2D = a
    Else
        Arr2D = rng.Value
    End If
End Function

Public Function Tbl(ByVal tName As String) As ListObject
    Dim ws As Worksheet, lo As ListObject
    For Each ws In ThisWorkbook.Worksheets
        For Each lo In ws.ListObjects
            If StrComp(lo.Name, tName, vbTextCompare) = 0 Then
                Set Tbl = lo
                Exit Function
            End If
        Next lo
    Next ws
    Err.Raise vbObjectError + 513, , "Table not found: " & tName
End Function

' Row (as a range) of table tName whose column col equals v, or Nothing.
Public Function FindInTable(ByVal tName As String, ByVal col As Long, ByVal v As String) As Range
    Dim lo As ListObject, a As Variant, i As Long
    Set lo = Tbl(tName)
    If lo.ListRows.Count = 0 Then Exit Function
    a = Arr2D(lo.ListColumns(col).DataBodyRange)
    For i = 1 To UBound(a, 1)
        If StrComp(Trim$(SafeText(a(i, 1))), v, vbTextCompare) = 0 Then
            Set FindInTable = lo.DataBodyRange.Rows(i)
            Exit Function
        End If
    Next i
End Function

Public Function Msg(ByVal key As String) As String
    Dim r As Range
    Set r = FindInTable("tblMsg", 1, key)
    If r Is Nothing Then Msg = key Else Msg = SafeText(r.Cells(1, 2).Value)
End Function

Public Function NR(ByVal nm As String) As Range
    Set NR = ThisWorkbook.Names(nm).RefersToRange
End Function

Public Function LocalRange(ByVal ws As Worksheet, ByVal nm As String) As Range
    On Error Resume Next
    Set LocalRange = ws.Names(nm).RefersToRange
    On Error GoTo 0
End Function

Public Function ColIndex(ByVal lo As ListObject, ByVal nm As String) As Long
    Dim c As ListColumn
    For Each c In lo.ListColumns
        If StrComp(c.Name, nm, vbTextCompare) = 0 Then
            ColIndex = c.Index
            Exit Function
        End If
    Next c
End Function

Public Function SheetOf(ByVal key As String) As Worksheet
    Dim r As Range
    Set r = FindInTable("tblPerms", 1, key)
    If r Is Nothing Then Err.Raise vbObjectError + 514, , "Unknown sheet key: " & key
    Set SheetOf = ThisWorkbook.Worksheets(SafeText(r.Cells(1, 2).Value))
End Function

Public Function KeyOfSheet(ByVal sheetName As String) As String
    Dim lo As ListObject, i As Long
    Set lo = Tbl("tblPerms")
    For i = 1 To lo.ListRows.Count
        If SafeText(lo.DataBodyRange.Cells(i, 2).Value) = sheetName Then
            KeyOfSheet = SafeText(lo.DataBodyRange.Cells(i, 1).Value)
            Exit Function
        End If
    Next i
End Function

Public Function RoleKey(ByVal labelOrKey As String) As String
    Dim lo As ListObject, i As Long
    Set lo = Tbl("tblRoles")
    For i = 1 To lo.ListRows.Count
        If SafeText(lo.DataBodyRange.Cells(i, 2).Value) = labelOrKey Or _
           StrComp(SafeText(lo.DataBodyRange.Cells(i, 1).Value), labelOrKey, vbTextCompare) = 0 Then
            RoleKey = SafeText(lo.DataBodyRange.Cells(i, 1).Value)
            Exit Function
        End If
    Next i
End Function

Public Function RoleLabel(ByVal key As String) As String
    Dim r As Range
    If key = "" Then Exit Function
    Set r = FindInTable("tblRoles", 1, key)
    If Not r Is Nothing Then RoleLabel = SafeText(r.Cells(1, 2).Value)
End Function

' Permission of the current user on a sheet key: E (edit), V (view) or N (hidden).
Public Function PermOf(ByVal key As String) As String
    Dim r As Range, lo As ListObject, c As Long
    Select Case key
        Case "LOGIN", "WARN", "LISTS"
            PermOf = "N": Exit Function
        Case "HOME"
            PermOf = IIf(CurrentUser = "", "N", "E"): Exit Function
    End Select
    If CurrentUser = "" Then PermOf = "N": Exit Function
    If CurrentRole = "Master" Then PermOf = "E": Exit Function
    Set lo = Tbl("tblPerms")
    c = ColIndex(lo, CurrentRole)
    Set r = FindInTable("tblPerms", 1, key)
    If c = 0 Or r Is Nothing Then PermOf = "N": Exit Function
    PermOf = UCase$(Trim$(SafeText(r.Cells(1, c).Value)))
    If PermOf <> "E" And PermOf <> "V" Then PermOf = "N"
End Function

Public Function CanEdit(ByVal key As String) As Boolean
    CanEdit = (PermOf(key) = "E")
End Function

Public Function CanView(ByVal key As String) As Boolean
    CanView = (PermOf(key) <> "N")
End Function

Public Function NewRow(ByVal lo As ListObject) As Range
    If lo.ListRows.Count = 1 Then
        If Application.WorksheetFunction.CountA(lo.DataBodyRange) = 0 Then
            Set NewRow = lo.DataBodyRange.Rows(1)
            Exit Function
        End If
    End If
    Set NewRow = lo.ListRows.Add(AlwaysInsert:=True).Range
End Function

Public Function DateText(ByVal d As Date) As String
    DateText = Format$(d, "yyyy-mm-dd")
End Function

' ============================================================ protection

Public Sub ProtectSheet(ByVal ws As Worksheet)
    ws.Protect Password:=PWD, DrawingObjects:=True, Contents:=True, Scenarios:=True, _
        UserInterfaceOnly:=True, AllowFormattingColumns:=True, AllowFormattingRows:=True, _
        AllowFiltering:=True
    ws.EnableSelection = xlNoRestrictions
End Sub

' Lock every cell of the sheet's main table, then unlock the columns listed in tblEditCols.
Private Sub ApplyEditCols(ByVal key As String, ByVal editable As Boolean)
    Dim ec As ListObject, i As Long, lo As ListObject, cols As Variant, j As Long
    Set ec = Tbl("tblEditCols")
    For i = 1 To ec.ListRows.Count
        If SafeText(ec.DataBodyRange.Cells(i, 1).Value) = key Then
            Set lo = Tbl(SafeText(ec.DataBodyRange.Cells(i, 2).Value))
            If lo.ListRows.Count > 0 Then
                lo.DataBodyRange.Locked = True
                If editable Then
                    cols = Split(SafeText(ec.DataBodyRange.Cells(i, 3).Value), ",")
                    For j = LBound(cols) To UBound(cols)
                        lo.ListColumns(CLng(Trim$(cols(j)))).DataBodyRange.Locked = False
                    Next j
                End If
            End If
        End If
    Next i
End Sub

Private Sub SetSheetMode(ByVal ws As Worksheet, ByVal perm As String)
    Dim key As String, rng As Range
    key = KeyOfSheet(ws.Name)
    ws.Unprotect PWD
    Set rng = LocalRange(ws, "InputArea")
    If Not rng Is Nothing Then rng.Locked = (perm <> "E")
    ApplyEditCols key, (perm = "E")
    Set rng = LocalRange(ws, "ParamArea")
    If Not rng Is Nothing Then rng.Locked = False
    ProtectSheet ws
    If perm = "E" Or perm = "V" Then
        ws.Visible = xlSheetVisible
    Else
        ws.Visible = xlSheetVeryHidden
    End If
End Sub

' Real form buttons, rebuilt from tblButtons on every open (so captions and macros stay in one table).
Private Sub InstallButtons()
    Dim ws As Worksheet, j As Long, lo As ListObject, i As Long, rng As Range, b As Object, vis As Long
    For Each ws In ThisWorkbook.Worksheets
        ws.Unprotect PWD
        For j = ws.Shapes.Count To 1 Step -1
            If Left$(ws.Shapes(j).Name, 4) = "btn_" Then ws.Shapes(j).Delete
        Next j
    Next ws
    Set lo = Tbl("tblButtons")
    For i = 1 To lo.ListRows.Count
        Set ws = SheetOf(SafeText(lo.DataBodyRange.Cells(i, 1).Value))
        vis = ws.Visible
        If vis <> xlSheetVisible Then ws.Visible = xlSheetVisible
        Set rng = ws.Range(SafeText(lo.DataBodyRange.Cells(i, 2).Value))
        Set b = ws.Buttons.Add(rng.Left + 2, rng.Top + 2, rng.Width - 4, rng.Height - 4)
        b.Name = "btn_" & i
        b.Caption = SafeText(lo.DataBodyRange.Cells(i, 3).Value)
        b.OnAction = SafeText(lo.DataBodyRange.Cells(i, 4).Value)
        b.Font.Bold = True
        b.Font.Size = 11
        b.Placement = xlMove
        If vis <> xlSheetVisible Then ws.Visible = vis
    Next i
End Sub

' ============================================================ login / roles

Public Sub LockDown()
    Dim lo As ListObject, i As Long, ws As Worksheet, login As Worksheet
    On Error GoTo EH
    BeginWork
    CurrentUser = ""
    CurrentRole = ""
    ThisWorkbook.Unprotect PWD
    Set login = SheetOf("LOGIN")
    login.Visible = xlSheetVisible
    InstallButtons
    Set lo = Tbl("tblPerms")
    For i = 1 To lo.ListRows.Count
        Set ws = ThisWorkbook.Worksheets(SafeText(lo.DataBodyRange.Cells(i, 2).Value))
        If ws.Name = login.Name Then SetSheetMode ws, "E" Else SetSheetMode ws, "N"
    Next i
    NR("lgnPass").Value = ""
    ThisWorkbook.Protect Password:=PWD, Structure:=True
    login.Activate
    NR("lgnUser").Select
    EndWork
    Exit Sub
EH:
    EndWork
    MsgW "LockDown: " & Err.Description, MB_ICONERROR
End Sub

Public Sub BtnLogin()
    Dim u As String, p As String, r As Range, rk As String
    On Error GoTo EH
    EnsureEvents
    u = Trim$(SafeText(NR("lgnUser").Value))
    p = SafeText(NR("lgnPass").Value)
    BeginWork
    NR("lgnPass").Value = ""
    EndWork
    If u = "" Then GoTo Bad
    Set r = FindInTable("tblUsers", 1, u)
    If r Is Nothing Then GoTo Bad
    If SafeText(r.Cells(1, 4).Value) <> Msg("YES") Then GoTo Bad
    If SafeText(r.Cells(1, 5).Value) <> PassHash(u, p) Then GoTo Bad
    rk = RoleKey(SafeText(r.Cells(1, 3).Value))
    If rk = "" Then Fail "ERR_ROLE": Exit Sub
    CurrentUser = SafeText(r.Cells(1, 1).Value)
    CurrentRole = rk
    ApplyRole
    WriteLog "LOGIN", "", "", "", ""
    Exit Sub
Bad:
    WriteLog "LOGIN_FAIL", "", "", "", u
    Fail "ERR_LOGIN"
    Exit Sub
EH:
    EndWork
    MsgW Msg("ERR_UNEXPECTED") & vbLf & Err.Description, MB_ICONERROR
End Sub

Public Sub ApplyRole()
    Dim lo As ListObject, i As Long, key As String, home As Worksheet
    On Error GoTo EH
    BeginWork
    Set lo = Tbl("tblPerms")
    If CurrentRole <> "Master" Then
        If ColIndex(lo, CurrentRole) = 0 Then
            EndWork
            Fail "ERR_ROLE"
            LockDown
            Exit Sub
        End If
    End If
    ThisWorkbook.Unprotect PWD
    Set home = SheetOf("HOME")
    home.Visible = xlSheetVisible
    For i = 1 To lo.ListRows.Count
        key = SafeText(lo.DataBodyRange.Cells(i, 1).Value)
        SetSheetMode ThisWorkbook.Worksheets(SafeText(lo.DataBodyRange.Cells(i, 2).Value)), PermOf(key)
    Next i
    ThisWorkbook.Protect Password:=PWD, Structure:=True
    BuildHome
    home.Activate
    EndWork
    Exit Sub
EH:
    EndWork
    MsgW "ApplyRole: " & Err.Description, MB_ICONERROR
End Sub

Private Sub BuildHome()
    Dim ws As Worksheet, lo As ListObject, i As Long, r As Long, key As String, perm As String, sName As String
    Dim u As Range
    Set ws = SheetOf("HOME")
    ws.Unprotect PWD
    ws.Range("B6:C40").Hyperlinks.Delete
    ws.Range("B6:C40").ClearContents
    Set u = FindInTable("tblUsers", 1, CurrentUser)
    NR("hmWelcome").Value = Msg("WELCOME") & " " & SafeText(u.Cells(1, 2).Value) & "  (" & RoleLabel(CurrentRole) & ")"
    Set lo = Tbl("tblPerms")
    r = 6
    For i = 1 To lo.ListRows.Count
        key = SafeText(lo.DataBodyRange.Cells(i, 1).Value)
        sName = SafeText(lo.DataBodyRange.Cells(i, 2).Value)
        perm = PermOf(key)
        If key <> "HOME" And perm <> "N" Then
            ws.Hyperlinks.Add Anchor:=ws.Cells(r, 2), Address:="", _
                SubAddress:="'" & sName & "'!A1", TextToDisplay:=sName
            ws.Cells(r, 2).Font.Size = 12
            ws.Cells(r, 3).Value = IIf(perm = "E", Msg("PERM_E"), Msg("PERM_V"))
            r = r + 1
        End If
    Next i
    ProtectSheet ws
End Sub

Public Sub BtnLogout()
    EnsureEvents
    If CurrentUser <> "" Then WriteLog "LOGOUT", "", "", "", ""
    LockDown
End Sub

Public Sub GoHome()
    If Not RequireLogin() Then Exit Sub
    SheetOf("HOME").Activate
End Sub

' Simple salted hash so passwords are not stored as plain text. Not real cryptography.
Public Function PassHash(ByVal u As String, ByVal p As String) As String
    Const M As Double = 2147483629#
    Dim s As String, i As Long, h As Double
    s = LCase$(u) & "|" & p
    h = 5381#
    For i = 1 To Len(s)
        h = h * 33# + (AscW(Mid$(s, i, 1)) And &HFFFF&)
        h = h - Int(h / M) * M
        If h < 0 Then h = h + M
        If h >= M Then h = h - M
    Next i
    PassHash = "H" & Format$(h, "0")
End Function

Public Sub BtnChangePassword()
    Dim r As Range, o As String, n1 As String, n2 As String
    If Not RequireLogin() Then Exit Sub
    o = SafeText(NR("hmOld").Value)
    n1 = SafeText(NR("hmNew").Value)
    n2 = SafeText(NR("hmNew2").Value)
    BeginWork
    NR("hmOld").Value = "": NR("hmNew").Value = "": NR("hmNew2").Value = ""
    EndWork
    Set r = FindInTable("tblUsers", 1, CurrentUser)
    If PassHash(CurrentUser, o) <> SafeText(r.Cells(1, 5).Value) Then Fail "ERR_OLD_PASS": Exit Sub
    If Len(n1) < 4 Then Fail "ERR_PASS_SHORT": Exit Sub
    If n1 <> n2 Then Fail "ERR_PASS_MATCH": Exit Sub
    BeginWork
    r.Cells(1, 5).Value = PassHash(CurrentUser, n1)
    EndWork
    WriteLog "PASS_CHANGE", "", "", "", CurrentUser
    MsgW Msg("OK_PASS")
End Sub

' ============================================================ users / permissions

Public Sub BtnSavePasswords()
    Dim lo As ListObject, i As Long, j As Long, u As String, np As String, masters As Long, cnt As Long
    If Not RequireLogin() Then Exit Sub
    If Not CanEdit("USERS") Then NoPerm: Exit Sub
    Set lo = Tbl("tblUsers")
    For i = 1 To lo.ListRows.Count
        u = Trim$(SafeText(lo.DataBodyRange.Cells(i, 1).Value))
        If u <> "" Then
            For j = i + 1 To lo.ListRows.Count
                If StrComp(u, Trim$(SafeText(lo.DataBodyRange.Cells(j, 1).Value)), vbTextCompare) = 0 Then
                    Fail "ERR_DUP_USER", " " & u: Exit Sub
                End If
            Next j
            If RoleKey(SafeText(lo.DataBodyRange.Cells(i, 3).Value)) = "" Then Fail "ERR_ROLE", " " & u: Exit Sub
            np = SafeText(lo.DataBodyRange.Cells(i, 6).Value)
            If np <> "" And Len(np) < 4 Then Fail "ERR_PASS_SHORT", " " & u: Exit Sub
            If np = "" And SafeText(lo.DataBodyRange.Cells(i, 5).Value) = "" Then Fail "ERR_NO_PASS", " " & u: Exit Sub
            If RoleKey(SafeText(lo.DataBodyRange.Cells(i, 3).Value)) = "Master" And _
               SafeText(lo.DataBodyRange.Cells(i, 4).Value) = Msg("YES") Then masters = masters + 1
        End If
    Next i
    If masters = 0 Then Fail "ERR_NO_MASTER": Exit Sub
    BeginWork
    For i = 1 To lo.ListRows.Count
        u = Trim$(SafeText(lo.DataBodyRange.Cells(i, 1).Value))
        np = SafeText(lo.DataBodyRange.Cells(i, 6).Value)
        If u <> "" And np <> "" Then
            lo.DataBodyRange.Cells(i, 5).Value = PassHash(u, np)
            lo.DataBodyRange.Cells(i, 6).Value = ""
            cnt = cnt + 1
            WriteLog "PASS_SET", lo.Parent.Name, "", "", u
        End If
    Next i
    EndWork
    MsgW Msg("OK_SAVED") & " (" & cnt & ")"
End Sub

Public Sub BtnApplyPerms()
    Dim lo As ListObject, i As Long, j As Long, v As String
    If Not RequireLogin() Then Exit Sub
    If Not CanEdit("PERMS") Then NoPerm: Exit Sub
    Set lo = Tbl("tblPerms")
    For i = 1 To lo.ListRows.Count
        For j = 4 To lo.ListColumns.Count
            v = UCase$(Trim$(SafeText(lo.DataBodyRange.Cells(i, j).Value)))
            If v <> "E" And v <> "V" And v <> "N" And v <> "-" Then
                Fail "ERR_PERM_VALUE", " " & SafeText(lo.DataBodyRange.Cells(i, 2).Value)
                Exit Sub
            End If
        Next j
    Next i
    WriteLog "PERMS_APPLY", lo.Parent.Name, "", "", ""
    ApplyRole
    MsgW Msg("OK_PERMS")
End Sub

' Adds a row to the active sheet's main table (the one listed in tblEditCols).
Public Sub BtnAddRow()
    Dim key As String, r As Range, lo As ListObject, rw As Range, c As Long, ws As Worksheet
    If Not RequireLogin() Then Exit Sub
    Set ws = ActiveSheet
    key = KeyOfSheet(ws.Name)
    If Not CanEdit(key) Then NoPerm: Exit Sub
    Set r = FindInTable("tblEditCols", 1, key)
    If r Is Nothing Then Exit Sub
    Set lo = Tbl(SafeText(r.Cells(1, 2).Value))
    BeginWork
    ws.Unprotect PWD
    Set rw = lo.ListRows.Add(AlwaysInsert:=True).Range
    For c = 1 To lo.ListColumns.Count
        If lo.ListRows.Count > 1 Then
            If lo.DataBodyRange.Cells(1, c).HasFormula Then rw.Cells(1, c).Formula = lo.DataBodyRange.Cells(1, c).Formula
        End If
    Next c
    rw.Cells(1, 1).NumberFormat = "@"
    If key = "USERS" Then rw.Cells(1, 4).Value = Msg("YES")
    ApplyEditCols key, True
    ProtectSheet ws
    EndWork
    rw.Cells(1, 1).Select
    WriteLog "ROW_ADD", ws.Name, rw.Address(False, False), "", ""
End Sub

' ============================================================ chart of accounts

Public Function IsPostingAccount(ByVal code As String) As Boolean
    Dim lo As ListObject, a As Variant, i As Long, c As String, found As Boolean
    Set lo = Tbl("tblCOA")
    If lo.ListRows.Count = 0 Then Exit Function
    a = Arr2D(lo.ListColumns(1).DataBodyRange)
    For i = 1 To UBound(a, 1)
        c = Trim$(SafeText(a(i, 1)))
        If c = code Then
            found = True
        ElseIf Len(c) > Len(code) And Left$(c, Len(code)) = code Then
            Exit Function
        End If
    Next i
    IsPostingAccount = found
End Function

Public Function AccountName(ByVal code As String) As String
    Dim r As Range
    Set r = FindInTable("tblCOA", 1, code)
    If Not r Is Nothing Then AccountName = SafeText(r.Cells(1, 2).Value)
End Function

Public Function PartyExists(ByVal nm As String) As Boolean
    PartyExists = Not (FindInTable("tblParties", 1, nm) Is Nothing)
End Function

Public Sub BtnCOASort()
    Dim lo As ListObject
    If Not RequireLogin() Then Exit Sub
    If Not CanEdit("COA") Then NoPerm: Exit Sub
    Set lo = Tbl("tblCOA")
    BeginWork
    lo.Parent.Unprotect PWD
    With lo.Sort
        .SortFields.Clear
        .SortFields.Add Key:=lo.ListColumns(1).DataBodyRange, SortOn:=xlSortOnValues, Order:=xlAscending, _
            DataOption:=xlSortNormal
        .Header = xlYes
        .Apply
    End With
    ProtectSheet lo.Parent
    EndWork
End Sub

Public Sub BtnCOADelete()
    Dim lo As ListObject, i As Long, code As String, a As Variant, c As String, idx As Long
    If Not RequireLogin() Then Exit Sub
    If Not CanEdit("COA") Then NoPerm: Exit Sub
    Set lo = Tbl("tblCOA")
    If Intersect(ActiveCell, lo.DataBodyRange) Is Nothing Then Fail "ERR_SELECT_ROW": Exit Sub
    idx = ActiveCell.Row - lo.DataBodyRange.Row + 1
    code = Trim$(SafeText(lo.DataBodyRange.Cells(idx, 1).Value))
    a = Arr2D(lo.ListColumns(1).DataBodyRange)
    For i = 1 To UBound(a, 1)
        c = Trim$(SafeText(a(i, 1)))
        If Len(c) > Len(code) And Left$(c, Len(code)) = code And code <> "" Then Fail "ERR_HAS_CHILDREN": Exit Sub
    Next i
    If code <> "" Then
        If Not FindInTable("tblJE", JE_CODE, code) Is Nothing Then Fail "ERR_ACC_USED": Exit Sub
    End If
    If Not Ask(Msg("ASK_DELETE") & " " & code & " " & SafeText(lo.DataBodyRange.Cells(idx, 2).Value)) Then Exit Sub
    BeginWork
    lo.Parent.Unprotect PWD
    lo.ListRows(idx).Delete
    ProtectSheet lo.Parent
    EndWork
    WriteLog "ACC_DELETE", lo.Parent.Name, "", code, ""
End Sub

' ============================================================ journal entries

Public Function JEStatusOf(ByVal jeNo As String) As String
    Dim r As Range
    Set r = FindInTable("tblJE", JE_NO, jeNo)
    If Not r Is Nothing Then JEStatusOf = SafeText(r.Cells(1, JE_STATUS).Value)
End Function

Public Function NextJENo() As String
    NextJENo = NextDocNo("setNextJE", "JE-", "tblJE", JE_NO)
End Function

' Next free number "<prefix>000123" from a settings counter, skipping numbers already used in the table.
Public Function NextDocNo(ByVal setting As String, ByVal prefix As String, ByVal tName As String, ByVal col As Long) As String
    Dim n As Long
    n = CLng(NumVal(NR(setting).Value))
    If n < 1 Then n = 1
    Do While Not FindInTable(tName, col, prefix & Format$(n, "000000")) Is Nothing
        n = n + 1
    Loop
    NextDocNo = prefix & Format$(n, "000000")
    NR(setting).Value = n + 1
End Function

' Appends journal lines to tblJE (sheet must be unprotected by the caller).
Public Sub WriteJERows(ByVal lo As ListObject, ByVal jeNo As String, ByVal jeDate As Date, ByVal jeDesc As String, _
        codes() As String, parties() As String, drs() As Double, crs() As Double, notes() As String, ByVal n As Long, _
        ByVal statusLabel As String, ByVal createdBy As String, ByVal createdAt As Date)
    Dim k As Long, rw As Range
    For k = 1 To n
        Set rw = NewRow(lo)
        rw.Cells(1, JE_NO).Value = jeNo
        rw.Cells(1, JE_DATE).Value = jeDate
        rw.Cells(1, JE_DESC).Value = jeDesc
        rw.Cells(1, JE_CODE).NumberFormat = "@"
        rw.Cells(1, JE_CODE).Value = codes(k)
        rw.Cells(1, JE_ACC).Value = AccountName(codes(k))
        rw.Cells(1, JE_PARTY).Value = parties(k)
        If drs(k) <> 0 Then rw.Cells(1, JE_DR).Value = drs(k)
        If crs(k) <> 0 Then rw.Cells(1, JE_CR).Value = crs(k)
        rw.Cells(1, JE_NOTE).Value = notes(k)
        rw.Cells(1, JE_STATUS).Value = statusLabel
        rw.Cells(1, JE_BY).Value = createdBy
        rw.Cells(1, JE_AT).Value = createdAt
    Next k
End Sub

Public Sub DeleteJERows(ByVal lo As ListObject, ByVal jeNo As String)
    Dim i As Long
    For i = lo.ListRows.Count To 1 Step -1
        If SafeText(lo.DataBodyRange.Cells(i, JE_NO).Value) = jeNo Then lo.ListRows(i).Delete
    Next i
End Sub

Public Function NormalizeJENo(ByVal s As String) As String
    s = UCase$(Trim$(s))
    If s <> "" And IsNumeric(s) Then s = "JE-" & Format$(CLng(s), "000000")
    NormalizeJENo = s
End Function

Public Sub BtnJESaveDraft()
    SaveJE "ST_DRAFT"
End Sub

Public Sub BtnJESubmit()
    SaveJE "ST_PENDING"
End Sub

Private Sub SaveJE(ByVal stKey As String)
    Dim lines As Range, i As Long, n As Long, k As Long
    Dim codes(1 To 50) As String, parties(1 To 50) As String, notes(1 To 50) As String
    Dim drs(1 To 50) As Double, crs(1 To 50) As Double
    Dim code As String, pty As String, dr As Double, cr As Double, totDr As Double, totCr As Double
    Dim jeNo As String, curSt As String, jeDate As Date, jeDesc As String, lo As ListObject
    If Not RequireLogin() Then Exit Sub
    If Not CanEdit("JE_ENTRY") Then NoPerm: Exit Sub
    On Error GoTo EH
    If Not IsDate(NR("jeeDate").Value) Then Fail "ERR_DATE": Exit Sub
    jeDate = CDate(NR("jeeDate").Value)
    jeDesc = Trim$(SafeText(NR("jeeDesc").Value))
    If jeDesc = "" Then Fail "ERR_DESC": Exit Sub
    Set lines = NR("jeeLines")
    For i = 1 To lines.Rows.Count
        code = Trim$(SafeText(lines.Cells(i, 1).Value))
        pty = Trim$(SafeText(lines.Cells(i, 3).Value))
        dr = Round(NumVal(lines.Cells(i, 4).Value), 2)
        cr = Round(NumVal(lines.Cells(i, 5).Value), 2)
        If code <> "" Or dr <> 0 Or cr <> 0 Then
            If code = "" Then Fail "ERR_LINE_ACC", " " & i: Exit Sub
            If Not IsPostingAccount(code) Then Fail "ERR_LINE_POSTING", " " & i: Exit Sub
            If pty <> "" And Not PartyExists(pty) Then Fail "ERR_LINE_PARTY", " " & i: Exit Sub
            If dr < 0 Or cr < 0 Or (dr <> 0 And cr <> 0) Or (dr = 0 And cr = 0) Then Fail "ERR_LINE_AMT", " " & i: Exit Sub
            n = n + 1
            codes(n) = code: parties(n) = pty: drs(n) = dr: crs(n) = cr
            notes(n) = Trim$(SafeText(lines.Cells(i, 6).Value))
            totDr = totDr + dr: totCr = totCr + cr
        End If
    Next i
    If n < 2 Then Fail "ERR_TWO_LINES": Exit Sub
    If Round(totDr - totCr, 2) <> 0 Then Fail "ERR_UNBALANCED": Exit Sub
    jeNo = Trim$(SafeText(NR("jeeNo").Value))
    If jeNo <> "" Then
        curSt = JEStatusOf(jeNo)
        If curSt <> "" And curSt <> Msg("ST_DRAFT") And curSt <> Msg("ST_RETURNED") Then Fail "ERR_NOT_EDITABLE": Exit Sub
        If IsLinkedJE(jeNo) Then Fail "ERR_LINKED_DOC": Exit Sub
    End If

    BeginWork
    Set lo = Tbl("tblJE")
    lo.Parent.Unprotect PWD
    If jeNo = "" Then
        jeNo = NextJENo()
    Else
        DeleteJERows lo, jeNo
    End If
    WriteJERows lo, jeNo, jeDate, jeDesc, codes, parties, drs, crs, notes, n, Msg(stKey), CurrentUser, Now
    ProtectSheet lo.Parent
    NR("jeeNo").Value = jeNo
    NR("jeeStatus").Value = Msg(stKey)
    EndWork
    WriteLog IIf(stKey = "ST_PENDING", "JE_SUBMIT", "JE_DRAFT"), SheetOf("JE").Name, "", "", _
        jeNo & " / " & Format$(totDr, "#,##0.00")
    MsgW Msg("OK_JE_SAVED") & " " & jeNo
    If stKey = "ST_PENDING" Then ClearEntry
    Exit Sub
EH:
    EndWork
    MsgW Msg("ERR_UNEXPECTED") & vbLf & Err.Description, MB_ICONERROR
End Sub

Public Sub ClearEntry()
    Dim lines As Range
    BeginWork
    NR("jeeNo").Value = ""
    NR("jeeStatus").Value = ""
    NR("jeeNote").Value = ""
    NR("jeeLoad").Value = ""
    NR("jeeDate").Value = Date
    NR("jeeDesc").Value = ""
    Set lines = NR("jeeLines")
    lines.Columns(1).ClearContents
    lines.Columns(3).Resize(, 4).ClearContents
    EndWork
End Sub

Public Sub BtnJEClear()
    If Not RequireLogin() Then Exit Sub
    If Not CanEdit("JE_ENTRY") Then NoPerm: Exit Sub
    ClearEntry
End Sub

Public Sub BtnJELoad()
    Dim jeNo As String, lo As ListObject, a As Variant, i As Long, n As Long, lines As Range
    If Not RequireLogin() Then Exit Sub
    If Not CanEdit("JE_ENTRY") Then NoPerm: Exit Sub
    jeNo = NormalizeJENo(SafeText(NR("jeeLoad").Value))
    Set lo = Tbl("tblJE")
    If jeNo = "" Or lo.ListRows.Count = 0 Then Fail "ERR_NOT_FOUND": Exit Sub
    a = Arr2D(lo.DataBodyRange)
    For i = 1 To UBound(a, 1)
        If SafeText(a(i, JE_NO)) = jeNo Then n = n + 1
    Next i
    If n = 0 Then Fail "ERR_NOT_FOUND": Exit Sub
    If IsLinkedJE(jeNo) Then Fail "ERR_LINKED_DOC": Exit Sub
    Set lines = NR("jeeLines")
    If n > lines.Rows.Count Then Fail "ERR_TOO_MANY": Exit Sub
    ClearEntry
    BeginWork
    n = 0
    For i = 1 To UBound(a, 1)
        If SafeText(a(i, JE_NO)) = jeNo Then
            n = n + 1
            If n = 1 Then
                NR("jeeDate").Value = a(i, JE_DATE)
                NR("jeeDesc").Value = a(i, JE_DESC)
                NR("jeeStatus").Value = a(i, JE_STATUS)
                NR("jeeNote").Value = a(i, JE_APPNOTE)
            End If
            lines.Cells(n, 1).NumberFormat = "@"
            lines.Cells(n, 1).Value = SafeText(a(i, JE_CODE))
            lines.Cells(n, 3).Value = a(i, JE_PARTY)
            lines.Cells(n, 4).Value = a(i, JE_DR)
            lines.Cells(n, 5).Value = a(i, JE_CR)
            lines.Cells(n, 6).Value = a(i, JE_NOTE)
        End If
    Next i
    NR("jeeNo").Value = jeNo
    EndWork
End Sub

' ============================================================ approvals

Public Sub BtnApprRefresh()
    If Not RequireLogin() Then Exit Sub
    If Not CanView("APPROVALS") Then NoPerm: Exit Sub
    RefreshApprovals
End Sub

Private Sub RefreshApprovals()
    Dim ws As Worksheet, lo As ListObject, a As Variant, i As Long, d As Object, k As String, v As Variant
    Dim stP As String, r As Long, key As Variant
    Set ws = SheetOf("APPROVALS")
    Set lo = Tbl("tblJE")
    stP = Msg("ST_PENDING")
    Set d = CreateObject("Scripting.Dictionary")
    If lo.ListRows.Count > 0 Then
        a = Arr2D(lo.DataBodyRange)
        For i = 1 To UBound(a, 1)
            If SafeText(a(i, JE_STATUS)) = stP Then
                k = SafeText(a(i, JE_NO))
                If d.Exists(k) Then
                    v = d(k)
                    v(2) = v(2) + NumVal(a(i, JE_DR))
                    d(k) = v
                Else
                    d.Add k, Array(a(i, JE_DATE), a(i, JE_DESC), NumVal(a(i, JE_DR)), a(i, JE_BY), a(i, JE_AT))
                End If
            End If
        Next i
    End If
    BeginWork
    ws.Range(ws.Cells(AP_FIRST, 2), ws.Cells(AP_LAST, 9)).ClearContents
    ws.Range(ws.Cells(AP_FIRST, 11), ws.Cells(AP_LAST, 16)).ClearContents
    r = AP_FIRST
    For Each key In d.Keys
        If r > AP_LAST Then Exit For
        v = d(key)
        ws.Cells(r, 2).Value = key
        ws.Cells(r, 3).Value = v(0)
        ws.Cells(r, 4).Value = v(1)
        ws.Cells(r, 5).Value = v(2)
        ws.Cells(r, 6).Value = v(3)
        ws.Cells(r, 7).Value = v(4)
        r = r + 1
    Next key
    EndWork
    If d.Count = 0 Then MsgW Msg("OK_NO_PENDING")
End Sub

Public Sub BtnApprExecute()
    Dim ws As Worksheet, lo As ListObject, r As Long, i As Long, jeNo As String, act As String, note As String
    Dim newSt As String, logKey As String, done As Long, skipped As Long, cnt As Long
    If Not RequireLogin() Then Exit Sub
    If Not CanEdit("APPROVALS") Then NoPerm: Exit Sub
    On Error GoTo EH
    Set ws = SheetOf("APPROVALS")
    Set lo = Tbl("tblJE")
    BeginWork
    For r = AP_FIRST To AP_LAST
        jeNo = SafeText(ws.Cells(r, 2).Value)
        act = SafeText(ws.Cells(r, 8).Value)
        note = Trim$(SafeText(ws.Cells(r, 9).Value))
        If jeNo <> "" And act <> "" Then
            newSt = ""
            If act = Msg("ACT_APPROVE") Then newSt = Msg("ST_APPROVED"): logKey = "JE_APPROVE"
            If act = Msg("ACT_REJECT") Then newSt = Msg("ST_REJECTED"): logKey = "JE_REJECT"
            If act = Msg("ACT_RETURN") Then newSt = Msg("ST_RETURNED"): logKey = "JE_RETURN"
            If newSt = "" Then
                skipped = skipped + 1
            ElseIf (logKey = "JE_RETURN" Or logKey = "JE_REJECT") And note = "" Then
                skipped = skipped + 1
            ElseIf SafeText(ws.Cells(r, 6).Value) = CurrentUser And CurrentRole <> "Master" Then
                skipped = skipped + 1
            ElseIf JEStatusOf(jeNo) <> Msg("ST_PENDING") Then
                skipped = skipped + 1
            Else
                If logKey = "JE_APPROVE" Then
                    If Not RepostStockDoc(jeNo) Then
                        skipped = skipped + 1
                        GoTo NextRow
                    End If
                End If
                cnt = 0
                For i = 1 To lo.ListRows.Count
                    If SafeText(lo.DataBodyRange.Cells(i, JE_NO).Value) = jeNo Then
                        lo.DataBodyRange.Cells(i, JE_STATUS).Value = newSt
                        lo.DataBodyRange.Cells(i, JE_APPBY).Value = CurrentUser
                        lo.DataBodyRange.Cells(i, JE_APPAT).Value = Now
                        lo.DataBodyRange.Cells(i, JE_APPNOTE).Value = note
                        cnt = cnt + 1
                    End If
                Next i
                SyncDocStatus jeNo, newSt
                If cnt > 0 Then done = done + 1
                WriteLog logKey, SheetOf("JE").Name, "", Msg("ST_PENDING"), jeNo & " -> " & newSt & IIf(note = "", "", " / " & note)
            End If
        End If
NextRow:
    Next r
    EndWork
    RefreshApprovals
    MsgW Msg("OK_APPR_DONE") & " " & done & IIf(skipped > 0, vbLf & Msg("WARN_APPR_SKIPPED") & " " & skipped, "")
    Exit Sub
EH:
    EndWork
    MsgW Msg("ERR_UNEXPECTED") & vbLf & Err.Description, MB_ICONERROR
End Sub

Public Sub BtnApprLines()
    Dim ws As Worksheet, jeNo As String, lo As ListObject, a As Variant, i As Long, r As Long
    If Not RequireLogin() Then Exit Sub
    If Not CanView("APPROVALS") Then NoPerm: Exit Sub
    Set ws = SheetOf("APPROVALS")
    If Not ActiveSheet Is ws Or ActiveCell.Row < AP_FIRST Or ActiveCell.Row > AP_LAST Then Fail "ERR_SELECT_ROW": Exit Sub
    jeNo = SafeText(ws.Cells(ActiveCell.Row, 2).Value)
    If jeNo = "" Then Fail "ERR_SELECT_ROW": Exit Sub
    Set lo = Tbl("tblJE")
    a = Arr2D(lo.DataBodyRange)
    BeginWork
    ws.Range(ws.Cells(AP_FIRST, 11), ws.Cells(AP_LAST, 16)).ClearContents
    r = AP_FIRST
    For i = 1 To UBound(a, 1)
        If SafeText(a(i, JE_NO)) = jeNo And r <= AP_LAST Then
            ws.Cells(r, 11).Value = jeNo
            ws.Cells(r, 12).NumberFormat = "@"
            ws.Cells(r, 12).Value = SafeText(a(i, JE_CODE))
            ws.Cells(r, 13).Value = a(i, JE_ACC)
            ws.Cells(r, 14).Value = a(i, JE_DR)
            ws.Cells(r, 15).Value = a(i, JE_CR)
            ws.Cells(r, 16).Value = IIf(SafeText(a(i, JE_PARTY)) = "", a(i, JE_NOTE), SafeText(a(i, JE_PARTY)) & " - " & SafeText(a(i, JE_NOTE)))
            r = r + 1
        End If
    Next i
    EndWork
End Sub

' ============================================================ trial balance

Public Sub BtnTBRefresh()
    Dim ws As Worksheet, lo As ListObject, a As Variant, i As Long, r As Long, lvl As Long, maxLvl As Long
    Dim code As String, f As String, last As Long, c As Long
    If Not RequireLogin() Then Exit Sub
    If Not CanView("TB") Then NoPerm: Exit Sub
    Set ws = SheetOf("TB")
    If Not IsDate(NR("tbFrom").Value) Or Not IsDate(NR("tbTo").Value) Then Fail "ERR_DATE": Exit Sub
    maxLvl = CLng(NumVal(NR("tbLevel").Value))
    If maxLvl < 1 Or maxLvl > 4 Then maxLvl = 4
    Set lo = Tbl("tblCOA")
    a = Arr2D(lo.DataBodyRange)
    BeginWork
    ws.Range("B8:L2000").Clear
    r = 8
    For i = 1 To UBound(a, 1)
        code = Trim$(SafeText(a(i, 1)))
        lvl = CodeLevel(code)
        If code <> "" And lvl <= maxLvl Then
            ws.Cells(r, 2).NumberFormat = "@"
            ws.Cells(r, 2).Value = code
            ws.Cells(r, 3).Value = a(i, 2)
            ws.Cells(r, 3).IndentLevel = lvl - 1
            ws.Cells(r, 4).Value = lvl
            f = "jeCode,$B" & r & "&""*"",jeStatus,stApproved,jeDate,"
            ws.Cells(r, 11).Formula = "=SUMIFS(jeDr," & f & """<""&tbFrom)-SUMIFS(jeCr," & f & """<""&tbFrom)"
            ws.Cells(r, 7).Formula = "=SUMIFS(jeDr," & f & """>=""&tbFrom,jeDate,""<=""&tbTo)"
            ws.Cells(r, 8).Formula = "=SUMIFS(jeCr," & f & """>=""&tbFrom,jeDate,""<=""&tbTo)"
            ws.Cells(r, 12).Formula = "=K" & r & "+G" & r & "-H" & r
            ws.Cells(r, 5).Formula = "=MAX(K" & r & ",0)"
            ws.Cells(r, 6).Formula = "=MAX(-K" & r & ",0)"
            ws.Cells(r, 9).Formula = "=MAX(L" & r & ",0)"
            ws.Cells(r, 10).Formula = "=MAX(-L" & r & ",0)"
            If lvl <= 2 Then ws.Range(ws.Cells(r, 2), ws.Cells(r, 10)).Font.Bold = True
            If lvl = 1 Then ws.Range(ws.Cells(r, 2), ws.Cells(r, 10)).Interior.Color = RGB(221, 235, 247)
            r = r + 1
        End If
    Next i
    last = r - 1
    ws.Cells(r, 3).Value = Msg("TOTAL")
    For c = 5 To 10
        ws.Cells(r, c).Formula = "=SUMIFS(" & ws.Range(ws.Cells(8, c), ws.Cells(last, c)).Address & "," & _
            ws.Range(ws.Cells(8, 4), ws.Cells(last, 4)).Address & ",1)"
    Next c
    With ws.Range(ws.Cells(r, 2), ws.Cells(r, 10))
        .Font.Bold = True
        .Interior.Color = RGB(31, 78, 120)
        .Font.Color = RGB(255, 255, 255)
    End With
    ws.Range(ws.Cells(8, 5), ws.Cells(r, 12)).NumberFormat = "#,##0.00;(#,##0.00);-"
    ws.Range(ws.Cells(8, 2), ws.Cells(r, 10)).Borders.LineStyle = xlContinuous
    ws.Range(ws.Cells(8, 2), ws.Cells(r, 10)).Borders.Color = RGB(200, 200, 200)
    EndWork
    WriteLog "REPORT", ws.Name, "", "", DateText(NR("tbFrom").Value) & " : " & DateText(NR("tbTo").Value)
End Sub

Public Function CodeLevel(ByVal code As String) As Long
    Select Case Len(code)
        Case 1: CodeLevel = 1
        Case 2: CodeLevel = 2
        Case 3, 4: CodeLevel = 3
        Case Else: CodeLevel = 4
    End Select
End Function

' ============================================================ account / party statement

Public Sub BtnLedgerRefresh()
    Dim ws As Worksheet, acc As String, pty As String, dFrom As Date, dTo As Date, lo As ListObject, a As Variant
    Dim i As Long, j As Long, n As Long, idx() As Long, tmp As Long, opening As Double, bal As Double
    Dim outA() As Variant, code As String, ok As Boolean, r As Long, totDr As Double, totCr As Double
    If Not RequireLogin() Then Exit Sub
    If Not CanView("LEDGER") Then NoPerm: Exit Sub
    Set ws = SheetOf("LEDGER")
    acc = Trim$(SafeText(NR("lgAcc").Value))
    pty = Trim$(SafeText(NR("lgParty").Value))
    If acc = "" And pty = "" Then Fail "ERR_LEDGER_PARAMS": Exit Sub
    If acc <> "" And FindInTable("tblCOA", 1, acc) Is Nothing Then Fail "ERR_NO_ACCOUNT": Exit Sub
    If Not IsDate(NR("lgFrom").Value) Or Not IsDate(NR("lgTo").Value) Then Fail "ERR_DATE": Exit Sub
    dFrom = CDate(NR("lgFrom").Value): dTo = CDate(NR("lgTo").Value)
    Set lo = Tbl("tblJE")
    BeginWork
    ws.Range("B8:I20000").Clear
    If lo.ListRows.Count > 0 Then
        a = Arr2D(lo.DataBodyRange)
        ReDim idx(1 To UBound(a, 1))
        For i = 1 To UBound(a, 1)
            code = SafeText(a(i, JE_CODE))
            ok = (SafeText(a(i, JE_STATUS)) = Msg("ST_APPROVED"))
            If ok And acc <> "" Then ok = (Left$(code, Len(acc)) = acc)
            If ok And pty <> "" Then ok = (SafeText(a(i, JE_PARTY)) = pty)
            If ok And IsDate(a(i, JE_DATE)) Then
                If CDate(a(i, JE_DATE)) < dFrom Then
                    opening = opening + NumVal(a(i, JE_DR)) - NumVal(a(i, JE_CR))
                ElseIf CDate(a(i, JE_DATE)) <= dTo Then
                    n = n + 1
                    idx(n) = i
                End If
            End If
        Next i
        ' insertion sort by date, then JE number
        For i = 2 To n
            tmp = idx(i)
            j = i - 1
            Do While j >= 1
                If LedgerAfter(a, idx(j), tmp) Then
                    idx(j + 1) = idx(j)
                    j = j - 1
                Else
                    Exit Do
                End If
            Loop
            idx(j + 1) = tmp
        Next i
    End If
    ws.Cells(8, 5).Value = Msg("OPENING")
    ws.Cells(8, 8).Value = Abs(opening)
    ws.Cells(8, 9).Value = Nature(opening)
    ws.Range("B8:I8").Font.Bold = True
    bal = opening
    If n > 0 Then
        ReDim outA(1 To n, 1 To 8)
        For i = 1 To n
            j = idx(i)
            bal = bal + NumVal(a(j, JE_DR)) - NumVal(a(j, JE_CR))
            totDr = totDr + NumVal(a(j, JE_DR)): totCr = totCr + NumVal(a(j, JE_CR))
            outA(i, 1) = a(j, JE_DATE)
            outA(i, 2) = a(j, JE_NO)
            outA(i, 3) = "'" & SafeText(a(j, JE_CODE))
            outA(i, 4) = SafeText(a(j, JE_DESC)) & IIf(SafeText(a(j, JE_NOTE)) = "", "", " - " & SafeText(a(j, JE_NOTE))) & _
                IIf(pty = "" And SafeText(a(j, JE_PARTY)) <> "", " (" & SafeText(a(j, JE_PARTY)) & ")", "")
            outA(i, 5) = a(j, JE_DR)
            outA(i, 6) = a(j, JE_CR)
            outA(i, 7) = Abs(bal)
            outA(i, 8) = Nature(bal)
        Next i
        ws.Range(ws.Cells(9, 2), ws.Cells(8 + n, 9)).Value = outA
    End If
    r = 9 + n
    ws.Cells(r, 5).Value = Msg("TOTAL")
    ws.Cells(r, 6).Value = totDr
    ws.Cells(r, 7).Value = totCr
    ws.Cells(r, 8).Value = Abs(bal)
    ws.Cells(r, 9).Value = Nature(bal)
    With ws.Range(ws.Cells(r, 2), ws.Cells(r, 9))
        .Font.Bold = True
        .Interior.Color = RGB(31, 78, 120)
        .Font.Color = RGB(255, 255, 255)
    End With
    ws.Range(ws.Cells(8, 2), ws.Cells(r, 2)).NumberFormat = "yyyy-mm-dd"
    ws.Range(ws.Cells(8, 6), ws.Cells(r, 8)).NumberFormat = "#,##0.00;(#,##0.00);-"
    ws.Range(ws.Cells(8, 2), ws.Cells(r, 9)).Borders.LineStyle = xlContinuous
    ws.Range(ws.Cells(8, 2), ws.Cells(r, 9)).Borders.Color = RGB(200, 200, 200)
    EndWork
    WriteLog "REPORT", ws.Name, "", "", acc & " " & pty
End Sub

Private Function LedgerAfter(a As Variant, ByVal x As Long, ByVal y As Long) As Boolean
    If CDate(a(x, JE_DATE)) <> CDate(a(y, JE_DATE)) Then
        LedgerAfter = CDate(a(x, JE_DATE)) > CDate(a(y, JE_DATE))
    Else
        LedgerAfter = SafeText(a(x, JE_NO)) > SafeText(a(y, JE_NO))
    End If
End Function

Public Function Nature(ByVal v As Double) As String
    If Round(v, 2) > 0 Then
        Nature = Msg("DR")
    ElseIf Round(v, 2) < 0 Then
        Nature = Msg("CR")
    Else
        Nature = "-"
    End If
End Function

' ============================================================ financial statements

Private Function FSum(ByVal prefix As String, ByVal crMinusDr As Boolean, ByVal cumulative As Boolean) As String
    Dim crit As String, sCr As String, sDr As String
    If cumulative Then
        crit = ",jeStatus,stApproved,jeDate,""<=""&fsTo"
    Else
        crit = ",jeStatus,stApproved,jeDate,"">=""&fsFrom,jeDate,""<=""&fsTo"
    End If
    sCr = "SUMIFS(jeCr,jeCode,""" & prefix & "*""" & crit & ")"
    sDr = "SUMIFS(jeDr,jeCode,""" & prefix & "*""" & crit & ")"
    If crMinusDr Then FSum = "(" & sCr & "-" & sDr & ")" Else FSum = "(" & sDr & "-" & sCr & ")"
End Function

' Level-2 group codes whose class digit is in classes; onlyCodes / exceptCodes are comma lists.
Private Function GroupCodes(ByVal classes As String, ByVal onlyCodes As String, ByVal exceptCodes As String) As Collection
    Dim lo As ListObject, a As Variant, i As Long, c As String, col As New Collection
    Set lo = Tbl("tblCOA")
    a = Arr2D(lo.ListColumns(1).DataBodyRange)
    For i = 1 To UBound(a, 1)
        c = Trim$(SafeText(a(i, 1)))
        If Len(c) = 2 Then
            If InStr(1, classes, Left$(c, 1)) > 0 Then
                If (onlyCodes = "" Or InStr(1, "," & onlyCodes & ",", "," & c & ",") > 0) And _
                   InStr(1, "," & exceptCodes & ",", "," & c & ",") = 0 Then col.Add c
            End If
        End If
    Next i
    Set GroupCodes = col
End Function

' Writes a titled section of group lines and returns the row of its total.
Private Function FSSection(ByVal ws As Worksheet, ByRef r As Long, ByVal lc As Long, ByVal titleKey As String, _
        ByVal totalKey As String, ByVal groups As Collection, ByVal crMinusDr As Boolean, _
        ByVal cumulative As Boolean, Optional ByVal extraLabel As String = "", Optional ByVal extraFormula As String = "") As Long
    Dim g As Variant, first As Long
    ws.Cells(r, lc).Value = Msg(titleKey)
    ws.Cells(r, lc).Font.Bold = True
    ws.Cells(r, lc).Font.Color = RGB(31, 78, 120)
    r = r + 1
    first = r
    For Each g In groups
        ws.Cells(r, lc).Value = AccountName(CStr(g))
        ws.Cells(r, lc).IndentLevel = 1
        ws.Cells(r, lc + 1).NumberFormat = "@"
        ws.Cells(r, lc + 1).Value = CStr(g)
        ws.Cells(r, lc + 2).Formula = "=" & FSum(CStr(g), crMinusDr, cumulative)
        r = r + 1
    Next g
    If extraLabel <> "" Then
        ws.Cells(r, lc).Value = extraLabel
        ws.Cells(r, lc).IndentLevel = 1
        ws.Cells(r, lc + 2).Formula = "=" & extraFormula
        r = r + 1
    End If
    ws.Cells(r, lc).Value = Msg(totalKey)
    If r > first Then
        ws.Cells(r, lc + 2).Formula = "=SUM(" & ws.Range(ws.Cells(first, lc + 2), ws.Cells(r - 1, lc + 2)).Address(False, False) & ")"
    Else
        ws.Cells(r, lc + 2).Value = 0
    End If
    ws.Range(ws.Cells(r, lc), ws.Cells(r, lc + 2)).Font.Bold = True
    ws.Range(ws.Cells(r, lc), ws.Cells(r, lc + 2)).Borders(xlEdgeTop).LineStyle = xlContinuous
    FSSection = r
    r = r + 2
End Function

Private Sub FSResult(ByVal ws As Worksheet, ByRef r As Long, ByVal lc As Long, ByVal key As String, ByVal formula As String)
    ws.Cells(r, lc).Value = Msg(key)
    ws.Cells(r, lc + 2).Formula = formula
    With ws.Range(ws.Cells(r, lc), ws.Cells(r, lc + 2))
        .Font.Bold = True
        .Interior.Color = RGB(221, 235, 247)
    End With
    r = r + 2
End Sub

Public Sub BtnFSRefresh()
    Dim ws As Worksheet, r As Long, tS As Long, tC As Long, tA As Long, tL As Long, tI As Long, tE As Long
    Dim tAs As Long, tLi As Long, tEq As Long, rG As Long, rO As Long, cumPL As String
    If Not RequireLogin() Then Exit Sub
    If Not CanView("FS") Then NoPerm: Exit Sub
    Set ws = SheetOf("FS")
    If Not IsDate(NR("fsFrom").Value) Or Not IsDate(NR("fsTo").Value) Then Fail "ERR_DATE": Exit Sub
    BeginWork
    ws.Range("B6:H400").Clear
    ' ----- income statement (columns B:D)
    r = 6
    ws.Cells(r, 2).Value = Msg("IS_TITLE") & "  " & DateText(NR("fsFrom").Value) & " : " & DateText(NR("fsTo").Value)
    ws.Cells(r, 2).Font.Bold = True: ws.Cells(r, 2).Font.Size = 13
    r = r + 2
    tS = FSSection(ws, r, 2, "IS_SALES", "IS_NET_SALES", GroupCodes("4", "41,42", ""), True, False)
    tC = FSSection(ws, r, 2, "IS_COGS", "IS_TOTAL_COGS", GroupCodes("5", "", ""), False, False)
    rG = r: FSResult ws, r, 2, "IS_GROSS", "=D" & tS & "-D" & tC
    tA = FSSection(ws, r, 2, "IS_GA", "IS_TOTAL_GA", GroupCodes("6", "", ""), False, False)
    tL = FSSection(ws, r, 2, "IS_SELL", "IS_TOTAL_SELL", GroupCodes("7", "", ""), False, False)
    rO = r: FSResult ws, r, 2, "IS_OPERATING", "=D" & rG & "-D" & tA & "-D" & tL
    tI = FSSection(ws, r, 2, "IS_OTHER_INC", "IS_TOTAL_OTHER_INC", GroupCodes("4", "", "41,42"), True, False)
    tE = FSSection(ws, r, 2, "IS_OTHER_EXP", "IS_TOTAL_OTHER_EXP", GroupCodes("8", "", ""), False, False)
    FSResult ws, r, 2, "IS_NET", "=D" & rO & "+D" & tI & "-D" & tE
    ws.Range("D6:D" & r).NumberFormat = "#,##0.00;(#,##0.00);-"
    ' ----- balance sheet (columns F:H)
    r = 6
    ws.Cells(r, 6).Value = Msg("BS_TITLE") & "  " & DateText(NR("fsTo").Value)
    ws.Cells(r, 6).Font.Bold = True: ws.Cells(r, 6).Font.Size = 13
    r = r + 2
    tAs = FSSection(ws, r, 6, "BS_ASSETS", "BS_TOTAL_ASSETS", GroupCodes("1", "", ""), False, True)
    tLi = FSSection(ws, r, 6, "BS_LIAB", "BS_TOTAL_LIAB", GroupCodes("2", "", ""), True, True)
    cumPL = FSum("4", True, True) & "-" & FSum("5", False, True) & "-" & FSum("6", False, True) & "-" & _
        FSum("7", False, True) & "-" & FSum("8", False, True)
    tEq = FSSection(ws, r, 6, "BS_EQUITY", "BS_TOTAL_EQUITY", GroupCodes("3", "", ""), True, True, Msg("BS_UNCLOSED_PL"), cumPL)
    FSResult ws, r, 6, "BS_TOTAL_LE", "=H" & tLi & "+H" & tEq
    FSResult ws, r, 6, "BS_CHECK", "=ROUND(H" & tAs & "-H" & tLi & "-H" & tEq & ",2)"
    ws.Range("H6:H" & r).NumberFormat = "#,##0.00;(#,##0.00);-"
    EndWork
    WriteLog "REPORT", ws.Name, "", "", DateText(NR("fsFrom").Value) & " : " & DateText(NR("fsTo").Value)
End Sub

' ============================================================ log

Public Sub WriteLog(ByVal actKey As String, ByVal sheetName As String, ByVal addr As String, _
        ByVal oldV As String, ByVal newV As String)
    Dim lo As ListObject, rw As Range, ev As Boolean
    On Error GoTo EH
    ev = Application.EnableEvents
    Application.EnableEvents = False
    Set lo = Tbl("tblLog")
    lo.Parent.Unprotect PWD
    Set rw = NewRow(lo)
    rw.Cells(1, 1).Value = Now
    rw.Cells(1, 2).Value = IIf(CurrentUser = "", "-", CurrentUser)
    rw.Cells(1, 3).Value = RoleLabel(CurrentRole)
    rw.Cells(1, 4).Value = Msg("LOG_" & actKey)
    rw.Cells(1, 5).Value = sheetName
    rw.Cells(1, 6).Value = addr
    rw.Cells(1, 7).Value = "'" & Left$(oldV, 250)
    rw.Cells(1, 8).Value = "'" & Left$(newV, 250)
    ProtectSheet lo.Parent
EH:
    Application.EnableEvents = ev
End Sub

Public Function IsNoLog(ByVal ws As Worksheet, ByVal target As Range) As Boolean
    Dim r As Range, lo As ListObject
    Set r = LocalRange(ws, "NoLog")
    If Not r Is Nothing Then
        If Not Intersect(r, target) Is Nothing Then IsNoLog = True: Exit Function
    End If
    If KeyOfSheet(ws.Name) = "USERS" Then
        Set lo = Tbl("tblUsers")
        If lo.ListRows.Count > 0 Then
            If Not Intersect(target, lo.ListColumns(6).DataBodyRange) Is Nothing Then IsNoLog = True
        End If
    End If
End Function

' ============================================================ PDF / print

Private Sub PreparePage(ByVal ws As Worksheet)
    Dim r As Range, cols As String, lastRow As Long, footer As String
    Set r = FindInTable("tblPrint", 1, KeyOfSheet(ws.Name))
    If r Is Nothing Then cols = "A:Z" Else cols = SafeText(r.Cells(1, 2).Value)
    lastRow = ws.UsedRange.Row + ws.UsedRange.Rows.Count - 1
    footer = Replace(SafeText(NR("setFooter").Value), "&", "&&")
    ws.Unprotect PWD
    With ws.PageSetup
        .PrintArea = ws.Range(Split(cols, ":")(0) & "1:" & Split(cols, ":")(1) & lastRow).Address
        .CenterHeader = "&B" & Replace(SafeText(NR("setCompany").Value), "&", "&&")
        .CenterFooter = footer
        .LeftFooter = "&P / &N"
        .RightFooter = CurrentUser & "  " & Format$(Now, "yyyy-mm-dd hh:nn")
        .Orientation = xlLandscape
        .Zoom = False
        .FitToPagesWide = 1
        .FitToPagesTall = False
        .PrintTitleRows = ""
    End With
    ProtectSheet ws
End Sub

Public Sub BtnExportPDF()
    Dim ws As Worksheet, f As Variant
    If Not RequireLogin() Then Exit Sub
    On Error GoTo EH
    Set ws = ActiveSheet
    PreparePage ws
    f = Application.GetSaveAsFilename(InitialFileName:=KeyOfSheet(ws.Name) & "_" & Format$(Now, "yyyy-mm-dd_hhnn") & ".pdf", _
        FileFilter:="PDF (*.pdf), *.pdf")
    If VarType(f) <> vbString Then Exit Sub
    ws.ExportAsFixedFormat Type:=xlTypePDF, Filename:=f, Quality:=xlQualityStandard, _
        IncludeDocProperties:=False, IgnorePrintAreas:=False, OpenAfterPublish:=True
    WriteLog "PDF", ws.Name, "", "", CStr(f)
    Exit Sub
EH:
    MsgW Msg("ERR_UNEXPECTED") & vbLf & Err.Description, MB_ICONERROR
End Sub

Public Sub BtnPrint()
    Dim ws As Worksheet
    If Not RequireLogin() Then Exit Sub
    Set ws = ActiveSheet
    PreparePage ws
    WriteLog "PRINT", ws.Name, "", "", ""
    ws.PrintPreview
End Sub

' ============================================================ save in locked state

' Saves with only the warning sheet visible, so a copy opened without macros shows nothing else,
' then puts the screen back the way it was.
Public Sub SaveLocked(ByVal SaveAsUI As Boolean, ByRef Cancel As Boolean)
    Dim n As Long, i As Long, states() As Long, actName As String, warn As Worksheet, f As Variant
    Dim restoring As Boolean
    On Error GoTo EH
    Cancel = True
    n = ThisWorkbook.Worksheets.Count
    ReDim states(1 To n)
    actName = ActiveSheet.Name
    BeginWork
    For i = 1 To n
        states(i) = ThisWorkbook.Worksheets(i).Visible
    Next i
    ThisWorkbook.Unprotect PWD
    Set warn = SheetOf("WARN")
    warn.Visible = xlSheetVisible
    For i = 1 To n
        If ThisWorkbook.Worksheets(i).Name <> warn.Name Then ThisWorkbook.Worksheets(i).Visible = xlSheetVeryHidden
    Next i
    warn.Activate
    ThisWorkbook.Protect Password:=PWD, Structure:=True
    If SaveAsUI Then
        f = Application.GetSaveAsFilename(InitialFileName:=ThisWorkbook.Name, _
            FileFilter:="Excel Macro-Enabled Workbook (*.xlsm), *.xlsm")
        If VarType(f) = vbString Then ThisWorkbook.SaveAs Filename:=f, FileFormat:=xlOpenXMLWorkbookMacroEnabled
    Else
        ThisWorkbook.Save
    End If
Restore:
    restoring = True
    ThisWorkbook.Unprotect PWD
    For i = 1 To n
        If states(i) = xlSheetVisible Then ThisWorkbook.Worksheets(i).Visible = xlSheetVisible
    Next i
    For i = 1 To n
        If states(i) <> xlSheetVisible Then ThisWorkbook.Worksheets(i).Visible = states(i)
    Next i
    ThisWorkbook.Protect Password:=PWD, Structure:=True
    ThisWorkbook.Worksheets(actName).Activate
    ThisWorkbook.Saved = True
    EndWork
    Exit Sub
EH:
    MsgW Msg("ERR_UNEXPECTED") & vbLf & Err.Description, MB_ICONERROR
    If restoring Then EndWork: Exit Sub
    Resume Restore
End Sub
