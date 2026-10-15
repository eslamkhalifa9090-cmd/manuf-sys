VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
END
Attribute VB_Name = "clsWbEvents"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Option Explicit

' Workbook events live in this class so the whole system installs from two importable files.

Public WithEvents wb As Workbook
Attribute wb.VB_VarHelpID = -1

Private mSheet As String, mAddr As String, mVal As String

Private Sub wb_SheetSelectionChange(ByVal Sh As Object, ByVal Target As Range)
    mSheet = Sh.Name
    mAddr = Target.Address
    If Target.CountLarge = 1 Then mVal = SafeText(Target.Value) Else mVal = ""
End Sub

Private Sub wb_SheetChange(ByVal Sh As Object, ByVal Target As Range)
    Dim oldV As String, newV As String
    On Error GoTo Done
    If CurrentUser = "" Then GoTo Done
    If KeyOfSheet(Sh.Name) = "LOG" Then GoTo Done
    If IsNoLog(Sh, Target) Then GoTo Done
    If Target.CountLarge = 1 Then
        newV = SafeText(Target.Value)
        If Sh.Name = mSheet And Target.Address = mAddr Then oldV = mVal
        mSheet = Sh.Name: mAddr = Target.Address: mVal = newV
    Else
        newV = Msg("MULTI_CELLS") & " " & Target.CountLarge
    End If
    WriteLog "EDIT", Sh.Name, Target.Address(False, False), oldV, newV
Done:
End Sub

Private Sub wb_BeforeSave(ByVal SaveAsUI As Boolean, Cancel As Boolean)
    SaveLocked SaveAsUI, Cancel
End Sub
