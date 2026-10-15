Attribute VB_Name = "modDocs"
Option Explicit

' New Star - stock (weighted average), sales, cash vouchers, molds and their reports.
' Every document writes its own register rows plus one journal entry; approval goes through the
' approvals sheet like any journal entry, and stock documents are re-costed at the moment of approval.

' tblStock columns
Public Const ST_NO As Long = 1, ST_DATE As Long = 2, ST_TYPE As Long = 3, ST_CODE As Long = 4, ST_NAME As Long = 5
Public Const ST_INQ As Long = 6, ST_OUTQ As Long = 7, ST_COST As Long = 8, ST_INV As Long = 9, ST_OUTV As Long = 10
Public Const ST_PARTY As Long = 11, ST_ORDER As Long = 12, ST_NOTE As Long = 13, ST_JE As Long = 14
Public Const ST_STATUS As Long = 15, ST_BY As Long = 16

' tblSales columns
Public Const SL_NO As Long = 1, SL_DATE As Long = 2, SL_TYPE As Long = 3, SL_CUST As Long = 4, SL_DISPATCH As Long = 5
Public Const SL_ORDER As Long = 6, SL_DESC As Long = 7, SL_UNIT As Long = 8, SL_QTY As Long = 9, SL_PRICE As Long = 10
Public Const SL_VALUE As Long = 11, SL_JE As Long = 12, SL_STATUS As Long = 13, SL_BY As Long = 14

' tblItems / tblMolds / tblMoldUse columns used here
Private Const IT_CODE As Long = 1, IT_NAME As Long = 2, IT_ACC As Long = 5
Private Const MD_CODE As Long = 1, MD_NAME As Long = 2, MD_OWN As Long = 4, MD_COST As Long = 7, MD_RATE As Long = 11
Private Const MD_NBV As Long = 13
Private Const MU_DATE As Long = 1, MU_CODE As Long = 2, MU_ORDER As Long = 4, MU_UNITS As Long = 8, MU_JE As Long = 9

Private Const MAX_LINES As Long = 60

' ============================================================ shared helpers

Public Function IsLinkedJE(ByVal jeNo As String) As Boolean
    If Not FindInTable("tblStock", ST_JE, jeNo) Is Nothing Then IsLinkedJE = True: Exit Function
    If Not FindInTable("tblSales", SL_JE, jeNo) Is Nothing Then IsLinkedJE = True
End Function

' Keeps document registers in step with their journal entry after an approval action.
Public Sub SyncDocStatus(ByVal jeNo As String, ByVal newSt As String)
    Dim lo As ListObject, i As Long
    Set lo = Tbl("tblStock")
    For i = 1 To lo.ListRows.Count
        If SafeText(lo.DataBodyRange.Cells(i, ST_JE).Value) = jeNo Then lo.DataBodyRange.Cells(i, ST_STATUS).Value = newSt
    Next i
    Set lo = Tbl("tblSales")
    For i = 1 To lo.ListRows.Count
        If SafeText(lo.DataBodyRange.Cells(i, SL_JE).Value) = jeNo Then lo.DataBodyRange.Cells(i, SL_STATUS).Value = newSt
    Next i
    If newSt = Msg("ST_REJECTED") Then
        Set lo = Tbl("tblMoldUse")
        lo.Parent.Unprotect PWD
        For i = 1 To lo.ListRows.Count
            If SafeText(lo.DataBodyRange.Cells(i, MU_JE).Value) = jeNo Then lo.DataBodyRange.Cells(i, MU_JE).Value = ""
        Next i
        ProtectSheet lo.Parent
    End If
End Sub

Private Function DocTypeKey(ByVal labelOrKey As String) As String
    Dim lo As ListObject, i As Long
    Set lo = Tbl("tblDocTypes")
    For i = 1 To lo.ListRows.Count
        If SafeText(lo.DataBodyRange.Cells(i, 2).Value) = labelOrKey Or SafeText(lo.DataBodyRange.Cells(i, 1).Value) = labelOrKey Then
            DocTypeKey = SafeText(lo.DataBodyRange.Cells(i, 1).Value)
            Exit Function
        End If
    Next i
End Function

Private Function DocTypeLabel(ByVal key As String) As String
    Dim r As Range
    Set r = FindInTable("tblDocTypes", 1, key)
    If Not r Is Nothing Then DocTypeLabel = SafeText(r.Cells(1, 2).Value)
End Function

Private Function DocCounter(ByVal key As String) As String
    Dim r As Range
    Set r = FindInTable("tblDocTypes", 1, key)
    If Not r Is Nothing Then DocCounter = SafeText(r.Cells(1, 3).Value)
End Function

' Payment method label -> account ("" means on account: the party's control account).
Private Function PayAccount(ByVal label As String, ByVal partyAcc As String) As String
    Dim r As Range
    Set r = FindInTable("tblPay", 1, label)
    If r Is Nothing Then Exit Function
    PayAccount = SafeText(r.Cells(1, 2).Value)
    If PayAccount = "" Then PayAccount = partyAcc
End Function

Private Function ItemAccount(ByVal code As String) As String
    Dim r As Range
    Set r = FindInTable("tblItems", IT_CODE, code)
    If Not r Is Nothing Then ItemAccount = SafeText(r.Cells(1, IT_ACC).Value)
End Function

Private Function ItemName(ByVal code As String) As String
    Dim r As Range
    Set r = FindInTable("tblItems", IT_CODE, code)
    If Not r Is Nothing Then ItemName = SafeText(r.Cells(1, IT_NAME).Value)
End Function

' code -> Array(inQ, outQ, inV, outV, lastInCost) over approved stock rows.
Private Function StockTotals() As Object
    Dim d As Object, lo As ListObject, a As Variant, i As Long, k As String, v As Variant, stA As String
    Set d = CreateObject("Scripting.Dictionary")
    Set lo = Tbl("tblStock")
    stA = Msg("ST_APPROVED")
    If lo.ListRows.Count > 0 Then
        a = Arr2D(lo.DataBodyRange)
        For i = 1 To UBound(a, 1)
            If SafeText(a(i, ST_STATUS)) = stA Then
                k = SafeText(a(i, ST_CODE))
                If d.Exists(k) Then v = d(k) Else v = Array(0#, 0#, 0#, 0#, 0#)
                v(0) = v(0) + NumVal(a(i, ST_INQ))
                v(1) = v(1) + NumVal(a(i, ST_OUTQ))
                v(2) = v(2) + NumVal(a(i, ST_INV))
                v(3) = v(3) + NumVal(a(i, ST_OUTV))
                If NumVal(a(i, ST_INQ)) > 0 And NumVal(a(i, ST_INV)) > 0 Then v(4) = NumVal(a(i, ST_INV)) / NumVal(a(i, ST_INQ))
                d(k) = v
            End If
        Next i
    End If
    Set StockTotals = d
End Function

Private Function WACOf(ByVal t As Object, ByVal code As String) As Double
    Dim v As Variant, q As Double, vv As Double
    If Not t.Exists(code) Then Exit Function
    v = t(code)
    q = v(0) - v(1)
    vv = v(2) - v(3)
    If q > 0.000001 And vv > 0 Then WACOf = vv / q Else WACOf = v(4)
End Function

Private Function IsInLine(ByVal tk As String, ByVal dirLabel As String) As Boolean
    Select Case tk
        Case "PUR", "ADJ+", "OPN": IsInLine = True
        Case "CMP": IsInLine = (dirLabel = Msg("DIR_IN"))
        Case Else: IsInLine = False
    End Select
End Function

' Unit cost and values for every line: purchases at their price, issues at the current weighted average,
' compound output at the total cost of its inputs split by quantity.
Private Sub CostLines(ByVal tk As String, ByVal n As Long, codes() As String, qtys() As Double, prices() As Double, _
        isIn() As Boolean, unitC() As Double, vIn() As Double, vOut() As Double)
    Dim t As Object, i As Long, totOut As Double, totInQ As Double, lastIn As Long, acc As Double
    Set t = StockTotals()
    For i = 1 To n
        vIn(i) = 0: vOut(i) = 0: unitC(i) = 0
        If isIn(i) Then
            If tk = "PUR" Or tk = "OPN" Then
                unitC(i) = prices(i)
            ElseIf tk = "ADJ+" Then
                unitC(i) = WACOf(t, codes(i))
                If unitC(i) = 0 Then unitC(i) = prices(i)
            End If
            vIn(i) = Round(qtys(i) * unitC(i), 2)
            totInQ = totInQ + qtys(i)
            lastIn = i
        Else
            unitC(i) = WACOf(t, codes(i))
            vOut(i) = Round(qtys(i) * unitC(i), 2)
            totOut = totOut + vOut(i)
        End If
    Next i
    If tk = "CMP" And totInQ > 0 Then
        For i = 1 To n
            If isIn(i) Then
                If i = lastIn Then
                    vIn(i) = Round(totOut - acc, 2)
                Else
                    vIn(i) = Round(totOut * qtys(i) / totInQ, 2)
                    acc = acc + vIn(i)
                End If
                unitC(i) = vIn(i) / qtys(i)
            End If
        Next i
    End If
End Sub

' Journal lines of a stock document, item side grouped by inventory account.
Private Function StockJELines(ByVal tk As String, ByVal party As String, ByVal order As String, ByVal payAcc As String, _
        ByVal n As Long, codes() As String, isIn() As Boolean, vIn() As Double, vOut() As Double, _
        jc() As String, jp() As String, jd() As Double, jcr() As Double, jn() As String) As Long
    Dim inAcc As Object, outAcc As Object, i As Long, a As String, k As Variant, m As Long, totIn As Double, totOut As Double
    Dim counter As String, note As String
    Set inAcc = CreateObject("Scripting.Dictionary")
    Set outAcc = CreateObject("Scripting.Dictionary")
    For i = 1 To n
        a = ItemAccount(codes(i))
        If isIn(i) Then
            inAcc(a) = inAcc(a) + vIn(i): totIn = totIn + vIn(i)
        Else
            outAcc(a) = outAcc(a) + vOut(i): totOut = totOut + vOut(i)
        End If
    Next i
    counter = DocCounter(tk)
    If tk = "PUR" Then counter = payAcc
    note = IIf(order = "", "", Msg("ORDER") & " " & order)
    For Each k In inAcc.Keys
        m = m + 1: jc(m) = k: jp(m) = "": jd(m) = Round(inAcc(k), 2): jcr(m) = 0: jn(m) = ""
    Next k
    If tk = "ISS" Or tk = "SPR" Or tk = "ADJ-" Then
        m = m + 1: jc(m) = counter: jp(m) = party: jd(m) = Round(totOut, 2): jcr(m) = 0: jn(m) = note
    End If
    For Each k In outAcc.Keys
        m = m + 1: jc(m) = k: jp(m) = "": jd(m) = 0: jcr(m) = Round(outAcc(k), 2): jn(m) = ""
    Next k
    If tk = "PUR" Or tk = "ADJ+" Or tk = "OPN" Then
        m = m + 1: jc(m) = counter: jp(m) = party: jd(m) = 0: jcr(m) = Round(totIn, 2): jn(m) = note
    End If
    StockJELines = m
End Function

' ============================================================ stock documents

Public Sub BtnStockDraft()
    SaveStockDoc "ST_DRAFT"
End Sub

Public Sub BtnStockSubmit()
    SaveStockDoc "ST_PENDING"
End Sub

Private Sub SaveStockDoc(ByVal stKey As String)
    Dim lines As Range, i As Long, n As Long, tk As String, dt As Date, party As String, order As String
    Dim payLabel As String, payAcc As String, desc As String, docNo As String, jeNo As String, curSt As String
    Dim codes(1 To 20) As String, qtys(1 To 20) As Double, prices(1 To 20) As Double, isIn(1 To 20) As Boolean
    Dim notes(1 To 20) As String, unitC(1 To 20) As Double, vIn(1 To 20) As Double, vOut(1 To 20) As Double
    Dim jc(1 To MAX_LINES) As String, jp(1 To MAX_LINES) As String, jd(1 To MAX_LINES) As Double
    Dim jcr(1 To MAX_LINES) As Double, jn(1 To MAX_LINES) As String, m As Long
    Dim code As String, q As Double, dirL As String, nIn As Long, nOut As Long, tot As Double
    Dim loS As ListObject, loJ As ListObject, rw As Range, creator As String, r As Range
    If Not RequireLogin() Then Exit Sub
    If Not CanEdit("STOCK_ENTRY") Then NoPerm: Exit Sub
    On Error GoTo EH
    tk = DocTypeKey(SafeText(NR("stfType").Value))
    If tk = "" Or tk = "OPN" Then Fail "ERR_DOC_TYPE": Exit Sub
    If Not IsDate(NR("stfDate").Value) Then Fail "ERR_DATE": Exit Sub
    dt = CDate(NR("stfDate").Value)
    party = Trim$(SafeText(NR("stfParty").Value))
    order = Trim$(SafeText(NR("stfOrder").Value))
    desc = Trim$(SafeText(NR("stfDesc").Value))
    If party <> "" And Not PartyExists(party) Then Fail "ERR_LINE_PARTY", " -": Exit Sub
    If tk = "PUR" Then
        payLabel = SafeText(NR("stfPay").Value)
        payAcc = PayAccount(payLabel, "2101")
        If payAcc = "" Then Fail "ERR_PAY": Exit Sub
        If payAcc = "2101" And party = "" Then Fail "ERR_PARTY_REQUIRED": Exit Sub
    End If
    Set lines = NR("stfLines")
    For i = 1 To lines.Rows.Count
        code = Trim$(SafeText(lines.Cells(i, 1).Value))
        q = NumVal(lines.Cells(i, 6).Value)
        If code <> "" Or q <> 0 Then
            If code = "" Or ItemName(code) = "" Then Fail "ERR_ITEM", " " & i: Exit Sub
            If q <= 0 Then Fail "ERR_QTY", " " & i: Exit Sub
            dirL = SafeText(lines.Cells(i, 8).Value)
            If tk = "CMP" And dirL <> Msg("DIR_IN") And dirL <> Msg("DIR_OUT") Then Fail "ERR_DIR", " " & i: Exit Sub
            n = n + 1
            codes(n) = code: qtys(n) = q: prices(n) = NumVal(lines.Cells(i, 7).Value)
            isIn(n) = IsInLine(tk, dirL)
            notes(n) = Trim$(SafeText(lines.Cells(i, 9).Value))
            If tk = "PUR" And prices(n) <= 0 Then Fail "ERR_PRICE", " " & i: Exit Sub
            If isIn(n) Then nIn = nIn + 1 Else nOut = nOut + 1
        End If
    Next i
    If n = 0 Then Fail "ERR_NO_LINES": Exit Sub
    If tk = "CMP" And (nIn = 0 Or nOut = 0) Then Fail "ERR_CMP_LINES": Exit Sub
    CostLines tk, n, codes, qtys, prices, isIn, unitC, vIn, vOut
    For i = 1 To n
        tot = tot + vIn(i) + vOut(i)
        If Not isIn(i) And unitC(i) = 0 Then Fail "ERR_NO_COST", " " & codes(i): Exit Sub
    Next i
    If tot = 0 Then Fail "ERR_NO_COST", "": Exit Sub
    m = StockJELines(tk, party, order, payAcc, n, codes, isIn, vIn, vOut, jc, jp, jd, jcr, jn)

    docNo = Trim$(SafeText(NR("stfNo").Value))
    If docNo <> "" Then
        Set r = FindInTable("tblStock", ST_NO, docNo)
        If Not r Is Nothing Then
            curSt = SafeText(r.Cells(1, ST_STATUS).Value)
            If curSt <> Msg("ST_DRAFT") And curSt <> Msg("ST_RETURNED") Then Fail "ERR_NOT_EDITABLE": Exit Sub
            jeNo = SafeText(r.Cells(1, ST_JE).Value)
        End If
    End If
    If desc = "" Then desc = DocTypeLabel(tk)

    BeginWork
    Set loS = Tbl("tblStock")
    Set loJ = Tbl("tblJE")
    loS.Parent.Unprotect PWD
    loJ.Parent.Unprotect PWD
    If docNo = "" Then docNo = NextDocNo("setNextST", "ST-", "tblStock", ST_NO)
    DeleteRows loS, ST_NO, docNo
    If jeNo = "" Then jeNo = NextJENo() Else DeleteJERows loJ, jeNo
    For i = 1 To n
        Set rw = NewRow(loS)
        rw.Cells(1, ST_NO).Value = docNo
        rw.Cells(1, ST_DATE).Value = dt
        rw.Cells(1, ST_TYPE).Value = DocTypeLabel(tk)
        rw.Cells(1, ST_CODE).NumberFormat = "@"
        rw.Cells(1, ST_CODE).Value = codes(i)
        rw.Cells(1, ST_NAME).Value = ItemName(codes(i))
        If isIn(i) Then
            rw.Cells(1, ST_INQ).Value = qtys(i)
            rw.Cells(1, ST_INV).Value = vIn(i)
        Else
            rw.Cells(1, ST_OUTQ).Value = qtys(i)
            rw.Cells(1, ST_OUTV).Value = vOut(i)
        End If
        rw.Cells(1, ST_COST).Value = unitC(i)
        rw.Cells(1, ST_PARTY).Value = party
        rw.Cells(1, ST_ORDER).Value = order
        rw.Cells(1, ST_NOTE).Value = IIf(notes(i) = "", desc, notes(i))
        rw.Cells(1, ST_JE).Value = jeNo
        rw.Cells(1, ST_STATUS).Value = Msg(stKey)
        rw.Cells(1, ST_BY).Value = CurrentUser
    Next i
    WriteJERows loJ, jeNo, dt, DocTypeLabel(tk) & " " & docNo & " - " & desc, jc, jp, jd, jcr, jn, m, Msg(stKey), CurrentUser, Now
    ProtectSheet loS.Parent
    ProtectSheet loJ.Parent
    NR("stfNo").Value = docNo
    NR("stfStatus").Value = Msg(stKey)
    EndWork
    WriteLog IIf(stKey = "ST_PENDING", "DOC_SUBMIT", "DOC_DRAFT"), SheetOf("STOCK").Name, "", "", docNo & " / " & jeNo
    MsgW Msg("OK_DOC_SAVED") & " " & docNo & " (" & jeNo & ")"
    If stKey = "ST_PENDING" Then ClearStockForm
    Exit Sub
EH:
    EndWork
    MsgW Msg("ERR_UNEXPECTED") & vbLf & Err.Description, MB_ICONERROR
End Sub

Public Sub DeleteRows(ByVal lo As ListObject, ByVal col As Long, ByVal v As String)
    Dim i As Long
    For i = lo.ListRows.Count To 1 Step -1
        If SafeText(lo.DataBodyRange.Cells(i, col).Value) = v Then lo.ListRows(i).Delete
    Next i
End Sub

Private Sub ClearStockForm()
    Dim lines As Range
    BeginWork
    NR("stfNo").Value = "": NR("stfStatus").Value = "": NR("stfNote").Value = "": NR("stfLoad").Value = ""
    NR("stfParty").Value = "": NR("stfOrder").Value = "": NR("stfDesc").Value = ""
    NR("stfDate").Value = Date
    Set lines = NR("stfLines")
    lines.Columns(1).ClearContents
    lines.Columns(6).Resize(, 4).ClearContents
    EndWork
End Sub

Public Sub BtnStockClear()
    If Not RequireLogin() Then Exit Sub
    If Not CanEdit("STOCK_ENTRY") Then NoPerm: Exit Sub
    ClearStockForm
End Sub

Public Sub BtnStockLoad()
    Dim docNo As String, lo As ListObject, a As Variant, i As Long, n As Long, lines As Range, tk As String
    If Not RequireLogin() Then Exit Sub
    If Not CanEdit("STOCK_ENTRY") Then NoPerm: Exit Sub
    docNo = UCase$(Trim$(SafeText(NR("stfLoad").Value)))
    If docNo <> "" And IsNumeric(docNo) Then docNo = "ST-" & Format$(CLng(docNo), "000000")
    Set lo = Tbl("tblStock")
    If docNo = "" Or FindInTable("tblStock", ST_NO, docNo) Is Nothing Then Fail "ERR_NOT_FOUND": Exit Sub
    a = Arr2D(lo.DataBodyRange)
    Set lines = NR("stfLines")
    For i = 1 To UBound(a, 1)
        If SafeText(a(i, ST_NO)) = docNo Then n = n + 1
    Next i
    If n > lines.Rows.Count Then Fail "ERR_TOO_MANY": Exit Sub
    ClearStockForm
    BeginWork
    n = 0
    For i = 1 To UBound(a, 1)
        If SafeText(a(i, ST_NO)) = docNo Then
            n = n + 1
            If n = 1 Then
                tk = DocTypeKey(SafeText(a(i, ST_TYPE)))
                NR("stfDate").Value = a(i, ST_DATE)
                NR("stfType").Value = a(i, ST_TYPE)
                NR("stfParty").Value = a(i, ST_PARTY)
                NR("stfOrder").Value = a(i, ST_ORDER)
                NR("stfStatus").Value = a(i, ST_STATUS)
                NR("stfNote").Value = AppNoteOf(SafeText(a(i, ST_JE)))
                NR("stfDesc").Value = a(i, ST_NOTE)
            End If
            lines.Cells(n, 1).NumberFormat = "@"
            lines.Cells(n, 1).Value = SafeText(a(i, ST_CODE))
            If NumVal(a(i, ST_INQ)) > 0 Then
                lines.Cells(n, 6).Value = a(i, ST_INQ)
                If tk = "CMP" Then lines.Cells(n, 8).Value = Msg("DIR_IN")
            Else
                lines.Cells(n, 6).Value = a(i, ST_OUTQ)
                If tk = "CMP" Then lines.Cells(n, 8).Value = Msg("DIR_OUT")
            End If
            If tk = "PUR" Or tk = "ADJ+" Then lines.Cells(n, 7).Value = a(i, ST_COST)
            lines.Cells(n, 9).Value = a(i, ST_NOTE)
        End If
    Next i
    NR("stfNo").Value = docNo
    EndWork
End Sub

Private Function AppNoteOf(ByVal jeNo As String) As String
    Dim r As Range
    Set r = FindInTable("tblJE", JE_NO, jeNo)
    If Not r Is Nothing Then AppNoteOf = SafeText(r.Cells(1, JE_APPNOTE).Value)
End Function

' Called on approval: issues are re-costed at the weighted average of that moment and the entry rewritten.
' Returns False when an item has no cost to issue at.
Public Function RepostStockDoc(ByVal jeNo As String) As Boolean
    Dim loS As ListObject, loJ As ListObject, a As Variant, i As Long, n As Long, tk As String
    Dim idx(1 To 20) As Long, codes(1 To 20) As String, qtys(1 To 20) As Double, prices(1 To 20) As Double
    Dim isIn(1 To 20) As Boolean, unitC(1 To 20) As Double, vIn(1 To 20) As Double, vOut(1 To 20) As Double
    Dim jc(1 To MAX_LINES) As String, jp(1 To MAX_LINES) As String, jd(1 To MAX_LINES) As Double
    Dim jcr(1 To MAX_LINES) As Double, jn(1 To MAX_LINES) As String, m As Long
    Dim party As String, order As String, r As Range, jeDate As Date, jeDesc As String, creator As String, created As Date
    Dim st As String
    RepostStockDoc = True
    Set loS = Tbl("tblStock")
    If loS.ListRows.Count = 0 Then Exit Function
    a = Arr2D(loS.DataBodyRange)
    For i = 1 To UBound(a, 1)
        If SafeText(a(i, ST_JE)) = jeNo And n < 20 Then
            n = n + 1
            idx(n) = i
            If n = 1 Then
                tk = DocTypeKey(SafeText(a(i, ST_TYPE)))
                party = SafeText(a(i, ST_PARTY)): order = SafeText(a(i, ST_ORDER))
            End If
            codes(n) = SafeText(a(i, ST_CODE))
            isIn(n) = NumVal(a(i, ST_INQ)) > 0
            qtys(n) = IIf(isIn(n), NumVal(a(i, ST_INQ)), NumVal(a(i, ST_OUTQ)))
            prices(n) = NumVal(a(i, ST_COST))
        End If
    Next i
    If n = 0 Or tk = "PUR" Or tk = "OPN" Then Exit Function
    CostLines tk, n, codes, qtys, prices, isIn, unitC, vIn, vOut
    For i = 1 To n
        If Not isIn(i) And unitC(i) = 0 Then
            MsgW Msg("ERR_NO_COST") & " " & codes(i) & " (" & jeNo & ")", MB_ICONERROR
            RepostStockDoc = False
            Exit Function
        End If
    Next i
    m = StockJELines(tk, party, order, "", n, codes, isIn, vIn, vOut, jc, jp, jd, jcr, jn)
    Set r = FindInTable("tblJE", JE_NO, jeNo)
    jeDate = r.Cells(1, JE_DATE).Value
    jeDesc = SafeText(r.Cells(1, JE_DESC).Value)
    creator = SafeText(r.Cells(1, JE_BY).Value)
    created = r.Cells(1, JE_AT).Value
    st = SafeText(r.Cells(1, JE_STATUS).Value)
    loS.Parent.Unprotect PWD
    For i = 1 To n
        With loS.DataBodyRange.Rows(idx(i))
            .Cells(1, ST_COST).Value = unitC(i)
            If isIn(i) Then .Cells(1, ST_INV).Value = vIn(i) Else .Cells(1, ST_OUTV).Value = vOut(i)
        End With
    Next i
    ProtectSheet loS.Parent
    Set loJ = Tbl("tblJE")
    loJ.Parent.Unprotect PWD
    DeleteJERows loJ, jeNo
    WriteJERows loJ, jeNo, jeDate, jeDesc, jc, jp, jd, jcr, jn, m, st, creator, created
    ProtectSheet loJ.Parent
End Function

' ============================================================ sales invoices

Public Sub BtnSaleDraft()
    SaveSale "ST_DRAFT"
End Sub

Public Sub BtnSaleSubmit()
    SaveSale "ST_PENDING"
End Sub

Private Sub SaveSale(ByVal stKey As String)
    Dim lines As Range, i As Long, n As Long, isRet As Boolean, dt As Date, cust As String, payAcc As String
    Dim disc As Double, gross As Double, net As Double, docNo As String, jeNo As String, r As Range, curSt As String
    Dim desc As String, dispatch As String, rw As Range, loS As ListObject, loJ As ListObject
    Dim ds(1 To 20) As String, ords(1 To 20) As String, units(1 To 20) As String, qs(1 To 20) As Double
    Dim ps(1 To 20) As Double, vs(1 To 20) As Double
    Dim jc(1 To 4) As String, jp(1 To 4) As String, jd(1 To 4) As Double, jcr(1 To 4) As Double, jn(1 To 4) As String, m As Long
    If Not RequireLogin() Then Exit Sub
    If Not CanEdit("SALES_ENTRY") Then NoPerm: Exit Sub
    On Error GoTo EH
    If SafeText(NR("sfType").Value) = Msg("SL_RETURN") Then
        isRet = True
    ElseIf SafeText(NR("sfType").Value) <> Msg("SL_INVOICE") Then
        Fail "ERR_DOC_TYPE": Exit Sub
    End If
    If Not IsDate(NR("sfDate").Value) Then Fail "ERR_DATE": Exit Sub
    dt = CDate(NR("sfDate").Value)
    cust = Trim$(SafeText(NR("sfCust").Value))
    If cust = "" Or Not PartyExists(cust) Then Fail "ERR_PARTY_REQUIRED": Exit Sub
    payAcc = PayAccount(SafeText(NR("sfPay").Value), "1401")
    If payAcc = "" Then Fail "ERR_PAY": Exit Sub
    disc = Round(NumVal(NR("sfDisc").Value), 2)
    If disc < 0 Then Fail "ERR_LINE_AMT", " -": Exit Sub
    dispatch = Trim$(SafeText(NR("sfDispatch").Value))
    desc = Trim$(SafeText(NR("sfDesc").Value))
    Set lines = NR("sfLines")
    For i = 1 To lines.Rows.Count
        If Trim$(SafeText(lines.Cells(i, 1).Value)) <> "" Or NumVal(lines.Cells(i, 4).Value) <> 0 Then
            If NumVal(lines.Cells(i, 4).Value) <= 0 Or NumVal(lines.Cells(i, 5).Value) < 0 Then Fail "ERR_QTY", " " & i: Exit Sub
            n = n + 1
            ds(n) = Trim$(SafeText(lines.Cells(i, 1).Value))
            ords(n) = Trim$(SafeText(lines.Cells(i, 2).Value))
            units(n) = Trim$(SafeText(lines.Cells(i, 3).Value))
            qs(n) = NumVal(lines.Cells(i, 4).Value)
            ps(n) = NumVal(lines.Cells(i, 5).Value)
            vs(n) = Round(qs(n) * ps(n), 2)
            gross = gross + vs(n)
        End If
    Next i
    If n = 0 Then Fail "ERR_NO_LINES": Exit Sub
    net = Round(gross - disc, 2)
    If net < 0 Then Fail "ERR_LINE_AMT", " -": Exit Sub
    If isRet Then
        m = m + 1: jc(m) = "4201": jp(m) = cust: jd(m) = gross: jcr(m) = 0: jn(m) = dispatch
        If disc > 0 Then m = m + 1: jc(m) = "4202": jp(m) = cust: jd(m) = 0: jcr(m) = disc: jn(m) = ""
        If net > 0 Then m = m + 1: jc(m) = payAcc: jp(m) = cust: jd(m) = 0: jcr(m) = net: jn(m) = dispatch
    Else
        If net > 0 Then m = m + 1: jc(m) = payAcc: jp(m) = cust: jd(m) = net: jcr(m) = 0: jn(m) = dispatch
        If disc > 0 Then m = m + 1: jc(m) = "4202": jp(m) = cust: jd(m) = disc: jcr(m) = 0: jn(m) = ""
        m = m + 1: jc(m) = "4101": jp(m) = cust: jd(m) = 0: jcr(m) = gross: jn(m) = dispatch
    End If
    If gross = 0 Then Fail "ERR_NO_LINES": Exit Sub
    docNo = Trim$(SafeText(NR("sfNo").Value))
    If docNo <> "" Then
        Set r = FindInTable("tblSales", SL_NO, docNo)
        If Not r Is Nothing Then
            curSt = SafeText(r.Cells(1, SL_STATUS).Value)
            If curSt <> Msg("ST_DRAFT") And curSt <> Msg("ST_RETURNED") Then Fail "ERR_NOT_EDITABLE": Exit Sub
            jeNo = SafeText(r.Cells(1, SL_JE).Value)
        End If
    End If
    BeginWork
    Set loS = Tbl("tblSales")
    Set loJ = Tbl("tblJE")
    loS.Parent.Unprotect PWD
    loJ.Parent.Unprotect PWD
    If docNo = "" Then docNo = NextDocNo("setNextSI", "SI-", "tblSales", SL_NO)
    DeleteRows loS, SL_NO, docNo
    If jeNo = "" Then jeNo = NextJENo() Else DeleteJERows loJ, jeNo
    For i = 1 To n
        Set rw = NewRow(loS)
        rw.Cells(1, SL_NO).Value = docNo
        rw.Cells(1, SL_DATE).Value = dt
        rw.Cells(1, SL_TYPE).Value = IIf(isRet, Msg("SL_RETURN"), Msg("SL_INVOICE"))
        rw.Cells(1, SL_CUST).Value = cust
        rw.Cells(1, SL_DISPATCH).Value = dispatch
        rw.Cells(1, SL_ORDER).Value = ords(i)
        rw.Cells(1, SL_DESC).Value = ds(i)
        rw.Cells(1, SL_UNIT).Value = units(i)
        rw.Cells(1, SL_QTY).Value = IIf(isRet, -qs(i), qs(i))
        rw.Cells(1, SL_PRICE).Value = ps(i)
        rw.Cells(1, SL_VALUE).Value = IIf(isRet, -vs(i), vs(i))
        rw.Cells(1, SL_JE).Value = jeNo
        rw.Cells(1, SL_STATUS).Value = Msg(stKey)
        rw.Cells(1, SL_BY).Value = CurrentUser
    Next i
    If disc > 0 Then
        Set rw = NewRow(loS)
        rw.Cells(1, SL_NO).Value = docNo
        rw.Cells(1, SL_DATE).Value = dt
        rw.Cells(1, SL_TYPE).Value = IIf(isRet, Msg("SL_RETURN"), Msg("SL_INVOICE"))
        rw.Cells(1, SL_CUST).Value = cust
        rw.Cells(1, SL_DISPATCH).Value = dispatch
        rw.Cells(1, SL_DESC).Value = Msg("SL_DISCOUNT")
        rw.Cells(1, SL_VALUE).Value = IIf(isRet, disc, -disc)
        rw.Cells(1, SL_JE).Value = jeNo
        rw.Cells(1, SL_STATUS).Value = Msg(stKey)
        rw.Cells(1, SL_BY).Value = CurrentUser
    End If
    If desc = "" Then desc = cust
    WriteJERows loJ, jeNo, dt, IIf(isRet, Msg("SL_RETURN"), Msg("SL_INVOICE")) & " " & docNo & _
        IIf(dispatch = "", "", " - " & Msg("DISPATCH") & " " & dispatch) & " - " & desc, jc, jp, jd, jcr, jn, m, _
        Msg(stKey), CurrentUser, Now
    ProtectSheet loS.Parent
    ProtectSheet loJ.Parent
    NR("sfNo").Value = docNo
    NR("sfStatus").Value = Msg(stKey)
    EndWork
    WriteLog IIf(stKey = "ST_PENDING", "DOC_SUBMIT", "DOC_DRAFT"), SheetOf("SALES").Name, "", "", docNo & " / " & jeNo
    MsgW Msg("OK_DOC_SAVED") & " " & docNo & " (" & jeNo & ")"
    If stKey = "ST_PENDING" Then ClearSaleForm
    Exit Sub
EH:
    EndWork
    MsgW Msg("ERR_UNEXPECTED") & vbLf & Err.Description, MB_ICONERROR
End Sub

Private Sub ClearSaleForm()
    Dim lines As Range
    BeginWork
    NR("sfNo").Value = "": NR("sfStatus").Value = "": NR("sfNote").Value = "": NR("sfLoad").Value = ""
    NR("sfCust").Value = "": NR("sfDispatch").Value = "": NR("sfDisc").Value = "": NR("sfDesc").Value = ""
    NR("sfDate").Value = Date
    Set lines = NR("sfLines")
    lines.Columns(1).Resize(, 5).ClearContents
    lines.Columns(7).ClearContents
    EndWork
End Sub

Public Sub BtnSaleClear()
    If Not RequireLogin() Then Exit Sub
    If Not CanEdit("SALES_ENTRY") Then NoPerm: Exit Sub
    ClearSaleForm
End Sub

Public Sub BtnSaleLoad()
    Dim docNo As String, lo As ListObject, a As Variant, i As Long, n As Long, lines As Range
    If Not RequireLogin() Then Exit Sub
    If Not CanEdit("SALES_ENTRY") Then NoPerm: Exit Sub
    docNo = UCase$(Trim$(SafeText(NR("sfLoad").Value)))
    If docNo <> "" And IsNumeric(docNo) Then docNo = "SI-" & Format$(CLng(docNo), "000000")
    Set lo = Tbl("tblSales")
    If docNo = "" Or FindInTable("tblSales", SL_NO, docNo) Is Nothing Then Fail "ERR_NOT_FOUND": Exit Sub
    a = Arr2D(lo.DataBodyRange)
    Set lines = NR("sfLines")
    ClearSaleForm
    BeginWork
    For i = 1 To UBound(a, 1)
        If SafeText(a(i, SL_NO)) = docNo Then
            If n = 0 Then
                NR("sfDate").Value = a(i, SL_DATE)
                NR("sfType").Value = a(i, SL_TYPE)
                NR("sfCust").Value = a(i, SL_CUST)
                NR("sfDispatch").Value = a(i, SL_DISPATCH)
                NR("sfStatus").Value = a(i, SL_STATUS)
                NR("sfNote").Value = AppNoteOf(SafeText(a(i, SL_JE)))
                NR("sfPay").Value = PayLabelOfJE(SafeText(a(i, SL_JE)))
            End If
            If SafeText(a(i, SL_DESC)) = Msg("SL_DISCOUNT") And NumVal(a(i, SL_QTY)) = 0 Then
                NR("sfDisc").Value = Abs(NumVal(a(i, SL_VALUE)))
            ElseIf n < lines.Rows.Count Then
                n = n + 1
                lines.Cells(n, 1).Value = a(i, SL_DESC)
                lines.Cells(n, 2).Value = a(i, SL_ORDER)
                lines.Cells(n, 3).Value = a(i, SL_UNIT)
                lines.Cells(n, 4).Value = Abs(NumVal(a(i, SL_QTY)))
                lines.Cells(n, 5).Value = a(i, SL_PRICE)
            End If
        End If
    Next i
    NR("sfNo").Value = docNo
    EndWork
End Sub

' Payment method of a sales entry, read back from its customer-side account.
Private Function PayLabelOfJE(ByVal jeNo As String) As String
    Dim lo As ListObject, i As Long, j As Long, code As String, pay As ListObject
    Set lo = Tbl("tblJE")
    Set pay = Tbl("tblPay")
    For i = 1 To lo.ListRows.Count
        If SafeText(lo.DataBodyRange.Cells(i, JE_NO).Value) = jeNo Then
            code = SafeText(lo.DataBodyRange.Cells(i, JE_CODE).Value)
            If code = "1401" Then PayLabelOfJE = SafeText(pay.DataBodyRange.Cells(1, 1).Value): Exit Function
            For j = 1 To pay.ListRows.Count
                If SafeText(pay.DataBodyRange.Cells(j, 2).Value) = code And code <> "" Then
                    PayLabelOfJE = SafeText(pay.DataBodyRange.Cells(j, 1).Value)
                    Exit Function
                End If
            Next j
        End If
    Next i
End Function

' ============================================================ cash vouchers

Public Sub BtnVoucherSubmit()
    Dim isRec As Boolean, dt As Date, cashAcc As String, acc As String, party As String, amt As Double, desc As String
    Dim jc(1 To 2) As String, jp(1 To 2) As String, jd(1 To 2) As Double, jcr(1 To 2) As Double, jn(1 To 2) As String
    Dim lo As ListObject, jeNo As String
    If Not RequireLogin() Then Exit Sub
    If Not CanEdit("VOUCHER") Then NoPerm: Exit Sub
    On Error GoTo EH
    If SafeText(NR("vcType").Value) = Msg("VC_RECEIPT") Then
        isRec = True
    ElseIf SafeText(NR("vcType").Value) <> Msg("VC_PAYMENT") Then
        Fail "ERR_DOC_TYPE": Exit Sub
    End If
    If Not IsDate(NR("vcDate").Value) Then Fail "ERR_DATE": Exit Sub
    dt = CDate(NR("vcDate").Value)
    cashAcc = PayAccount(SafeText(NR("vcCash").Value), "")
    If cashAcc = "" Then Fail "ERR_PAY": Exit Sub
    acc = Trim$(SafeText(NR("vcAcc").Value))
    If Not IsPostingAccount(acc) Then Fail "ERR_LINE_POSTING", " -": Exit Sub
    If acc = cashAcc Then Fail "ERR_SAME_ACC": Exit Sub
    party = Trim$(SafeText(NR("vcParty").Value))
    If party <> "" And Not PartyExists(party) Then Fail "ERR_LINE_PARTY", " -": Exit Sub
    If (acc = "1401" Or acc = "2101") And party = "" Then Fail "ERR_PARTY_REQUIRED": Exit Sub
    amt = Round(NumVal(NR("vcAmount").Value), 2)
    If amt <= 0 Then Fail "ERR_LINE_AMT", " -": Exit Sub
    desc = Trim$(SafeText(NR("vcDesc").Value))
    If desc = "" Then Fail "ERR_DESC": Exit Sub
    If isRec Then
        jc(1) = cashAcc: jd(1) = amt: jc(2) = acc: jp(2) = party: jcr(2) = amt
    Else
        jc(1) = acc: jp(1) = party: jd(1) = amt: jc(2) = cashAcc: jcr(2) = amt
    End If
    BeginWork
    Set lo = Tbl("tblJE")
    lo.Parent.Unprotect PWD
    jeNo = NextJENo()
    WriteJERows lo, jeNo, dt, SafeText(NR("vcType").Value) & " - " & desc, jc, jp, jd, jcr, jn, 2, Msg("ST_PENDING"), CurrentUser, Now
    ProtectSheet lo.Parent
    NR("vcAmount").Value = "": NR("vcDesc").Value = "": NR("vcParty").Value = ""
    EndWork
    WriteLog "JE_SUBMIT", SheetOf("JE").Name, "", "", jeNo & " / " & Format$(amt, "#,##0.00")
    MsgW Msg("OK_JE_SAVED") & " " & jeNo
    Exit Sub
EH:
    EndWork
    MsgW Msg("ERR_UNEXPECTED") & vbLf & Err.Description, MB_ICONERROR
End Sub

' ============================================================ molds

' Monthly amortization of factory-owned molds from usage rows not yet posted: Dr 5209 / Cr 1206.
Public Sub BtnMoldAmortize()
    Dim loU As ListObject, loM As ListObject, a As Variant, i As Long, mDate As Date, y As Long, mo As Long
    Dim per As Object, k As Variant, r As Range, amt As Double, tot As Double, nbv As Double, rows As New Collection
    Dim jc(1 To MAX_LINES) As String, jp(1 To MAX_LINES) As String, jd(1 To MAX_LINES) As Double
    Dim jcr(1 To MAX_LINES) As Double, jn(1 To MAX_LINES) As String, m As Long, jeNo As String, loJ As ListObject
    Dim dt As Date, v As Variant
    If Not RequireLogin() Then Exit Sub
    If Not CanEdit("MOLD_USE") Then NoPerm: Exit Sub
    On Error GoTo EH
    If Not IsDate(NR("muMonth").Value) Then Fail "ERR_DATE": Exit Sub
    mDate = CDate(NR("muMonth").Value)
    y = Year(mDate): mo = Month(mDate)
    Set loU = Tbl("tblMoldUse")
    If loU.ListRows.Count = 0 Then MsgW Msg("OK_NOTHING"): Exit Sub
    a = Arr2D(loU.DataBodyRange)
    Set per = CreateObject("Scripting.Dictionary")
    For i = 1 To UBound(a, 1)
        If IsDate(a(i, MU_DATE)) And SafeText(a(i, MU_JE)) = "" And NumVal(a(i, MU_UNITS)) > 0 Then
            If Year(CDate(a(i, MU_DATE))) = y And Month(CDate(a(i, MU_DATE))) = mo Then
                k = SafeText(a(i, MU_CODE))
                per(k) = per(k) + NumVal(a(i, MU_UNITS))
                rows.Add i
            End If
        End If
    Next i
    For Each k In per.Keys
        Set r = FindInTable("tblMolds", MD_CODE, CStr(k))
        If Not r Is Nothing Then
            If SafeText(r.Cells(1, MD_OWN).Value) = Msg("OWN_FACTORY") And NumVal(r.Cells(1, MD_RATE).Value) > 0 Then
                amt = Round(per(k) * NumVal(r.Cells(1, MD_RATE).Value), 2)
                nbv = NumVal(r.Cells(1, MD_COST).Value) - PostedAmortization(CStr(k))
                If amt > nbv Then amt = Round(nbv, 2)
                If amt > 0 And m < MAX_LINES - 1 Then
                    m = m + 1: jc(m) = "1206": jcr(m) = amt: jn(m) = CStr(k) & " - " & SafeText(r.Cells(1, MD_NAME).Value)
                    tot = tot + amt
                End If
            End If
        End If
    Next k
    If tot = 0 Then MsgW Msg("OK_NOTHING"): Exit Sub
    m = m + 1: jc(m) = "5209": jd(m) = Round(tot, 2): jn(m) = Format$(mDate, "yyyy-mm")
    dt = DateSerial(y, mo + 1, 0)
    BeginWork
    Set loJ = Tbl("tblJE")
    loJ.Parent.Unprotect PWD
    jeNo = NextJENo()
    WriteJERows loJ, jeNo, dt, Msg("MOLD_JE") & " " & Format$(mDate, "yyyy-mm"), jc, jp, jd, jcr, jn, m, Msg("ST_PENDING"), CurrentUser, Now
    ProtectSheet loJ.Parent
    loU.Parent.Unprotect PWD
    For Each v In rows
        loU.DataBodyRange.Cells(v, MU_JE).Value = jeNo
    Next v
    ProtectSheet loU.Parent
    EndWork
    WriteLog "JE_SUBMIT", SheetOf("JE").Name, "", "", jeNo & " / " & Format$(tot, "#,##0.00")
    MsgW Msg("OK_JE_SAVED") & " " & jeNo
    Exit Sub
EH:
    EndWork
    MsgW Msg("ERR_UNEXPECTED") & vbLf & Err.Description, MB_ICONERROR
End Sub

' Amortization already in journal entries (not rejected) for one mold, from the line notes.
Private Function PostedAmortization(ByVal moldCode As String) As Double
    Dim lo As ListObject, a As Variant, i As Long, stR As String
    Set lo = Tbl("tblJE")
    If lo.ListRows.Count = 0 Then Exit Function
    a = Arr2D(lo.DataBodyRange)
    stR = Msg("ST_REJECTED")
    For i = 1 To UBound(a, 1)
        If SafeText(a(i, JE_CODE)) = "1206" And SafeText(a(i, JE_STATUS)) <> stR Then
            If Left$(SafeText(a(i, JE_NOTE)), Len(moldCode) + 3) = moldCode & " - " Then
                PostedAmortization = PostedAmortization + NumVal(a(i, JE_CR)) - NumVal(a(i, JE_DR))
            End If
        End If
    Next i
End Function

' ============================================================ reports

Public Sub BtnItemCard()
    Dim ws As Worksheet, code As String, dFrom As Date, dTo As Date, lo As ListObject, a As Variant, i As Long
    Dim n As Long, idx() As Long, j As Long, tmp As Long, q As Double, v As Double, outA() As Variant, stA As String
    Dim r As Long
    If Not RequireLogin() Then Exit Sub
    If Not CanView("ITEM_CARD") Then NoPerm: Exit Sub
    Set ws = SheetOf("ITEM_CARD")
    code = Trim$(SafeText(NR("icItem").Value))
    If ItemName(code) = "" Then Fail "ERR_ITEM", "": Exit Sub
    If Not IsDate(NR("icFrom").Value) Or Not IsDate(NR("icTo").Value) Then Fail "ERR_DATE": Exit Sub
    dFrom = CDate(NR("icFrom").Value): dTo = CDate(NR("icTo").Value)
    Set lo = Tbl("tblStock")
    stA = Msg("ST_APPROVED")
    BeginWork
    ws.Range("B8:N20000").Clear
    If lo.ListRows.Count > 0 Then
        a = Arr2D(lo.DataBodyRange)
        ReDim idx(1 To UBound(a, 1))
        For i = 1 To UBound(a, 1)
            If SafeText(a(i, ST_CODE)) = code And SafeText(a(i, ST_STATUS)) = stA And IsDate(a(i, ST_DATE)) Then
                If CDate(a(i, ST_DATE)) < dFrom Then
                    q = q + NumVal(a(i, ST_INQ)) - NumVal(a(i, ST_OUTQ))
                    v = v + NumVal(a(i, ST_INV)) - NumVal(a(i, ST_OUTV))
                ElseIf CDate(a(i, ST_DATE)) <= dTo Then
                    n = n + 1: idx(n) = i
                End If
            End If
        Next i
        For i = 2 To n
            tmp = idx(i): j = i - 1
            Do While j >= 1
                If CDate(a(idx(j), ST_DATE)) > CDate(a(tmp, ST_DATE)) Then idx(j + 1) = idx(j): j = j - 1 Else Exit Do
            Loop
            idx(j + 1) = tmp
        Next i
    End If
    ws.Cells(8, 5).Value = Msg("OPENING")
    ws.Cells(8, 11).Value = q
    ws.Cells(8, 12).Value = v
    ws.Cells(8, 13).Value = IIf(q > 0.000001, v / IIf(q = 0, 1, q), 0)
    ws.Range("B8:N8").Font.Bold = True
    If n > 0 Then
        ReDim outA(1 To n, 1 To 13)
        For i = 1 To n
            j = idx(i)
            q = q + NumVal(a(j, ST_INQ)) - NumVal(a(j, ST_OUTQ))
            v = v + NumVal(a(j, ST_INV)) - NumVal(a(j, ST_OUTV))
            outA(i, 1) = a(j, ST_DATE)
            outA(i, 2) = a(j, ST_NO)
            outA(i, 3) = a(j, ST_TYPE)
            outA(i, 4) = SafeText(a(j, ST_PARTY)) & IIf(SafeText(a(j, ST_ORDER)) = "", "", " / " & SafeText(a(j, ST_ORDER)))
            outA(i, 5) = a(j, ST_NOTE)
            outA(i, 6) = a(j, ST_INQ)
            outA(i, 7) = a(j, ST_OUTQ)
            outA(i, 8) = a(j, ST_COST)
            outA(i, 9) = a(j, ST_INV)
            outA(i, 10) = q
            outA(i, 11) = Round(v, 2)
            outA(i, 12) = IIf(q > 0.000001, v / IIf(q = 0, 1, q), 0)
            outA(i, 13) = a(j, ST_OUTV)
        Next i
        ws.Range(ws.Cells(9, 2), ws.Cells(8 + n, 14)).Value = outA
    End If
    r = 9 + n
    ws.Cells(r, 5).Value = Msg("BALANCE")
    ws.Cells(r, 11).Value = q
    ws.Cells(r, 12).Value = Round(v, 2)
    ws.Cells(r, 13).Value = IIf(q > 0.000001, v / IIf(q = 0, 1, q), 0)
    With ws.Range(ws.Cells(r, 2), ws.Cells(r, 14))
        .Font.Bold = True
        .Interior.Color = RGB(31, 78, 120)
        .Font.Color = RGB(255, 255, 255)
    End With
    ws.Range(ws.Cells(8, 2), ws.Cells(r, 2)).NumberFormat = "yyyy-mm-dd"
    ws.Range(ws.Cells(8, 7), ws.Cells(r, 8)).NumberFormat = "#,##0.###;-#,##0.###;-"
    ws.Range(ws.Cells(8, 9), ws.Cells(r, 9)).NumberFormat = "#,##0.00##"
    ws.Range(ws.Cells(8, 10), ws.Cells(r, 10)).NumberFormat = "#,##0.00;(#,##0.00);-"
    ws.Range(ws.Cells(8, 11), ws.Cells(r, 11)).NumberFormat = "#,##0.###;-#,##0.###;-"
    ws.Range(ws.Cells(8, 12), ws.Cells(r, 12)).NumberFormat = "#,##0.00;(#,##0.00);-"
    ws.Range(ws.Cells(8, 13), ws.Cells(r, 13)).NumberFormat = "#,##0.00##"
    ws.Range(ws.Cells(8, 14), ws.Cells(r, 14)).NumberFormat = "#,##0.00;(#,##0.00);-"
    ws.Range(ws.Cells(8, 2), ws.Cells(r, 14)).Borders.LineStyle = xlContinuous
    ws.Range(ws.Cells(8, 2), ws.Cells(r, 14)).Borders.Color = RGB(200, 200, 200)
    EndWork
    WriteLog "REPORT", ws.Name, "", "", code
End Sub

' Customer / supplier balances from approved lines on 1401 and 2101 up to a date.
Public Sub BtnPartyBalances()
    Dim ws As Worksheet, dTo As Date, lo As ListObject, a As Variant, i As Long, d As Object, k As Variant, v As Variant
    Dim code As String, p As String, stA As String, r As Long, outA() As Variant, n As Long, pr As Range
    Dim t1 As Double, t2 As Double
    If Not RequireLogin() Then Exit Sub
    If Not CanView("PARTY_BAL") Then NoPerm: Exit Sub
    Set ws = SheetOf("PARTY_BAL")
    If Not IsDate(NR("pbTo").Value) Then Fail "ERR_DATE": Exit Sub
    dTo = CDate(NR("pbTo").Value)
    Set lo = Tbl("tblJE")
    Set d = CreateObject("Scripting.Dictionary")
    stA = Msg("ST_APPROVED")
    If lo.ListRows.Count > 0 Then
        a = Arr2D(lo.DataBodyRange)
        For i = 1 To UBound(a, 1)
            code = SafeText(a(i, JE_CODE)): p = SafeText(a(i, JE_PARTY))
            If p <> "" And (code = "1401" Or code = "2101") And SafeText(a(i, JE_STATUS)) = stA And IsDate(a(i, JE_DATE)) Then
                If CDate(a(i, JE_DATE)) <= dTo Then
                    If d.Exists(p) Then v = d(p) Else v = Array(0#, 0#)
                    If code = "1401" Then v(0) = v(0) + NumVal(a(i, JE_DR)) - NumVal(a(i, JE_CR)) _
                        Else v(1) = v(1) + NumVal(a(i, JE_DR)) - NumVal(a(i, JE_CR))
                    d(p) = v
                End If
            End If
        Next i
    End If
    BeginWork
    ws.Range("B8:H2000").Clear
    n = d.Count
    If n > 0 Then
        ReDim outA(1 To n, 1 To 6)
        i = 0
        For Each k In d.Keys
            i = i + 1
            v = d(k)
            Set pr = FindInTable("tblParties", 1, CStr(k))
            outA(i, 1) = k
            If Not pr Is Nothing Then outA(i, 2) = pr.Cells(1, 2).Value
            outA(i, 3) = Round(v(0), 2)
            outA(i, 4) = Round(v(1), 2)
            outA(i, 5) = Round(Abs(v(0) + v(1)), 2)
            outA(i, 6) = Nature(v(0) + v(1))
            t1 = t1 + v(0): t2 = t2 + v(1)
        Next k
        ws.Range(ws.Cells(8, 2), ws.Cells(7 + n, 7)).Value = outA
        ws.Range(ws.Cells(8, 2), ws.Cells(7 + n, 7)).Sort Key1:=ws.Cells(8, 2), Order1:=xlAscending, Header:=xlNo
    End If
    r = 8 + n
    ws.Cells(r, 2).Value = Msg("TOTAL")
    ws.Cells(r, 4).Value = Round(t1, 2)
    ws.Cells(r, 5).Value = Round(t2, 2)
    ws.Cells(r, 6).Value = Round(Abs(t1 + t2), 2)
    ws.Cells(r, 7).Value = Nature(t1 + t2)
    With ws.Range(ws.Cells(r, 2), ws.Cells(r, 7))
        .Font.Bold = True
        .Interior.Color = RGB(31, 78, 120)
        .Font.Color = RGB(255, 255, 255)
    End With
    ws.Range(ws.Cells(8, 4), ws.Cells(r, 6)).NumberFormat = "#,##0.00;(#,##0.00);-"
    ws.Range(ws.Cells(8, 2), ws.Cells(r, 7)).Borders.LineStyle = xlContinuous
    ws.Range(ws.Cells(8, 2), ws.Cells(r, 7)).Borders.Color = RGB(200, 200, 200)
    EndWork
    WriteLog "REPORT", ws.Name, "", "", DateText(dTo)
End Sub

' Sales, returns, discounts, cost of sales and receipts per customer for a period.
Public Sub BtnSalesReport()
    Dim ws As Worksheet, dFrom As Date, dTo As Date, lo As ListObject, a As Variant, i As Long, d As Object
    Dim k As Variant, v As Variant, code As String, p As String, stA As String, n As Long, outA() As Variant, r As Long
    Dim dr As Double, cr As Double, tot(1 To 8) As Double, c As Long, receipts As Object, jeNo As String
    If Not RequireLogin() Then Exit Sub
    If Not CanView("SALES_REP") Then NoPerm: Exit Sub
    Set ws = SheetOf("SALES_REP")
    If Not IsDate(NR("srFrom").Value) Or Not IsDate(NR("srTo").Value) Then Fail "ERR_DATE": Exit Sub
    dFrom = CDate(NR("srFrom").Value): dTo = CDate(NR("srTo").Value)
    Set lo = Tbl("tblJE")
    Set d = CreateObject("Scripting.Dictionary")
    Set receipts = CreateObject("Scripting.Dictionary")
    stA = Msg("ST_APPROVED")
    If lo.ListRows.Count > 0 Then
        a = Arr2D(lo.DataBodyRange)
        ' entries that touch cash or bank are receipts / refunds
        For i = 1 To UBound(a, 1)
            If Left$(SafeText(a(i, JE_CODE)), 2) = "15" Then receipts(SafeText(a(i, JE_NO))) = True
        Next i
        For i = 1 To UBound(a, 1)
            code = SafeText(a(i, JE_CODE)): p = SafeText(a(i, JE_PARTY)): jeNo = SafeText(a(i, JE_NO))
            If p <> "" And SafeText(a(i, JE_STATUS)) = stA And IsDate(a(i, JE_DATE)) Then
                If CDate(a(i, JE_DATE)) >= dFrom And CDate(a(i, JE_DATE)) <= dTo Then
                    dr = NumVal(a(i, JE_DR)): cr = NumVal(a(i, JE_CR))
                    If code = "4101" Or code = "4201" Or code = "4202" Or code = "5101" Or _
                       (code = "1401" And receipts.Exists(jeNo)) Then
                        If d.Exists(p) Then v = d(p) Else v = Array(0#, 0#, 0#, 0#, 0#)
                        Select Case code
                            Case "4101": v(0) = v(0) + cr - dr
                            Case "4201": v(1) = v(1) + dr - cr
                            Case "4202": v(2) = v(2) + dr - cr
                            Case "5101": v(3) = v(3) + dr - cr
                            Case "1401": v(4) = v(4) + cr - dr
                        End Select
                        d(p) = v
                    End If
                End If
            End If
        Next i
    End If
    BeginWork
    ws.Range("B8:K2000").Clear
    n = d.Count
    If n > 0 Then
        ReDim outA(1 To n, 1 To 9)
        i = 0
        For Each k In d.Keys
            i = i + 1
            v = d(k)
            outA(i, 1) = k
            outA(i, 2) = Round(v(0), 2)
            outA(i, 3) = Round(v(1), 2)
            outA(i, 4) = Round(v(2), 2)
            outA(i, 5) = Round(v(0) - v(1) - v(2), 2)
            outA(i, 6) = Round(v(3), 2)
            outA(i, 7) = Round(v(0) - v(1) - v(2) - v(3), 2)
            If outA(i, 5) <> 0 Then outA(i, 8) = outA(i, 7) / outA(i, 5) Else outA(i, 8) = 0
            outA(i, 9) = Round(v(4), 2)
            For c = 2 To 7
                tot(c) = tot(c) + outA(i, c)
            Next c
            tot(8) = tot(8) + outA(i, 9)
        Next k
        ws.Range(ws.Cells(8, 2), ws.Cells(7 + n, 10)).Value = outA
        ws.Range(ws.Cells(8, 2), ws.Cells(7 + n, 10)).Sort Key1:=ws.Cells(8, 6), Order1:=xlDescending, Header:=xlNo
    End If
    r = 8 + n
    ws.Cells(r, 2).Value = Msg("TOTAL")
    For c = 2 To 7
        ws.Cells(r, c + 1).Value = Round(tot(c), 2)
    Next c
    If tot(5) <> 0 Then ws.Cells(r, 9).Value = tot(7) / tot(5)
    ws.Cells(r, 10).Value = Round(tot(8), 2)
    With ws.Range(ws.Cells(r, 2), ws.Cells(r, 10))
        .Font.Bold = True
        .Interior.Color = RGB(31, 78, 120)
        .Font.Color = RGB(255, 255, 255)
    End With
    ws.Range(ws.Cells(8, 3), ws.Cells(r, 8)).NumberFormat = "#,##0.00;(#,##0.00);-"
    ws.Range(ws.Cells(8, 9), ws.Cells(r, 9)).NumberFormat = "0.0%"
    ws.Range(ws.Cells(8, 10), ws.Cells(r, 10)).NumberFormat = "#,##0.00;(#,##0.00);-"
    ws.Range(ws.Cells(8, 2), ws.Cells(r, 10)).Borders.LineStyle = xlContinuous
    ws.Range(ws.Cells(8, 2), ws.Cells(r, 10)).Borders.Color = RGB(200, 200, 200)
    EndWork
    WriteLog "REPORT", ws.Name, "", "", DateText(dFrom) & " : " & DateText(dTo)
End Sub
