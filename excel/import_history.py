"""Turn the original NeW StAr workbook (April-July 2026) into Excel-edition journal entries and stock rows.

Sources and the rules taken from them:
- Cash book (تفصيلى خزينة) is the only source of receipts and payments; the payment rows inside the sales and
  purchases sheets duplicate it and are skipped. Cash account by payment method (كاش 1501, فودافون 1502, انستا 1503).
- Sales sheet: invoices (Dr 1401 / Cr 4101), returns (Dr 4201), discounts allowed (Dr 4202), other income (4304).
- Purchases sheet: stock items into inventory at the invoice price, appliances to 1103, discounts received 4302.
- Stores sheet: issues to orders at weighted average cost (Dr 5101), compound batches (تضريبة) costed at the
  average cost of their inputs, count differences to 4303 / 5403.
Everything is dated as in the workbook and processed in date order, so the weighted average is perpetual.
"""
import collections
import pickle
import re
from datetime import date, datetime

OPENING_DATE = date(2026, 3, 31)
CASH_ACC = {"كاش": "1501", "فودافون": "1502", "انستا": "1503", None: "1501"}
CASH_COUNTER = {
    "أجور ومرتبات": "5201", "إعلانات وكروت": "7101", "إيرادات أخرى": "4304", "اكراميات": "6105", "ايجارات": "6102",
    "تأمينات مقدمة": "1601", "توزيعات أرباح": "3302", "سولار سيارات ومعدات": "6109", "ضيافه": "6104",
    "كهرباء/مياه": "6103", "مسحوبات شخصية": "3201", "مصاريف صيانه": "6108", "مصروفات عامة": "6114",
    "مطبوعات وأدوات مكتبية": "6107", "نقل وشحن وانتقالات": "6110", "هاتف وبريد وانترنت": "6106",
    "دفعات عملاء": "1401", "دفعات موردين": "2101",
}
PARTY_ALIAS = {"عمر غزالى": "عمر غازولى", "الغازولى": "عمر غازولى", "بيكو": "بيكو - البنهاوى",
               "البنهاوى": "بيكو - البنهاوى", "ياسين ايمن": "ياسين ايمن (ممرض)", "مجدى السيسى": "مجدى السيسي",
               "احمد كمال": "ا/ احمد كمال CNC", "حسين الجمل": "حسين الجمل - باريس",
               "محمد على": "محمد على الزقازيق", "محمد NFC": "محمد عبدالدايم", "محمد": "محمد المصرى",
               "محمد المصرى": "محمد المصرى"}
STOCK_GROUPS = {"خامات": "خامات", "قطع غيار ومهمات": "قطع غيار ومهمات", "مستلزمات تعبئة": "مستلزمات تعبئة",
                "انتاج تام": "إنتاج تام"}
COMPOUND = "تضريبة شفافة"


def _t(v):
    return "" if v is None else str(v).strip()


def _d(v):
    return v.date() if isinstance(v, datetime) else v


class History:
    """Collects documents; `je` and `stock` hold rows in the Excel tables' column order (without numbers)."""

    def __init__(self, wb_pickle, opening_items, parties):
        self.d = pickle.load(open(wb_pickle, "rb"))
        self.parties = set(parties)
        self.items = {it["name"]: it for it in opening_items}  # name -> item dict (code, group, unit, account)
        self.group_acc = None
        self.qty = collections.defaultdict(float)
        self.val = collections.defaultdict(float)
        self.docs = []   # (date, order, kind, payload)
        self.issues = []
        self.new_parties = []
        self.dtls = {_t(r[1]): r for r in self.d["ملخص مخازن"][3:455] if r[1]}

    # ---------------------------------------------------------------- items
    def item_for(self, code, fallback_name, store, unit):
        code = _t(code)
        if code.startswith("تضريبة"):
            name, store, unit = COMPOUND, "خامات", "كيلو"
        else:
            r = self.dtls.get(code)
            name = _t(r[3]) if r is not None and _t(r[3]) not in ("", "0") else (_t(fallback_name) or code)
            if r is not None:
                store, unit = store or _t(r[0]), unit or _t(r[4])
        if name not in self.items:
            group = STOCK_GROUPS.get(store)
            if group is None:
                return None
            self.items[name] = {"code": None, "name": name, "group": group, "unit": unit or "عدد",
                                "account": self.group_acc[group], "qty": 0.0, "cost": 0.0, "new": True}
        return self.items[name]

    def party(self, name, default):
        name = _t(name)
        name = PARTY_ALIAS.get(name, name)
        if name in self.parties:
            return name
        if name and default == "":
            self.parties.add(name)
            self.new_parties.append(name)
            self.issues.append(f"طرف جديد «{name}» اتضاف لشيت العملاء والموردين")
            return name
        if name:
            self.issues.append(f"طرف غير معروف «{name}» اتسجل على «{default}»")
        return default

    # ---------------------------------------------------------------- collect
    def collect(self):
        d = self.d
        for i, r in enumerate(r for r in d["تفصيلى خزينة"][4:363] if r[1]):
            if _d(r[1]) <= OPENING_DATE:
                continue
            self.docs.append((_d(r[1]), 3, i, "cash", r))
        sales = [r for r in d["تفصيلى مبيعات"][3:569] if r[2]]
        inv = collections.OrderedDict()
        for r in sales:
            kind = _t(r[6])
            if kind == "مبيعات":
                inv.setdefault((_d(r[2]), r[3], _t(r[4])), []).append(r)
            elif kind in ("مرتجع مبيعات", "خصم مسموح به", "إيردات آخرى"):
                self.docs.append((_d(r[2]), 1, len(self.docs), "sale_adj", r))
        for k, rows in inv.items():
            self.docs.append((k[0], 1, len(self.docs), "sale", rows))
        pur = collections.OrderedDict()
        for r in d["تفصيلى مشتريات"][3:499]:
            if not r[1] or _t(r[3]) == "1 افتتاحى":
                continue
            if _t(r[6]) == "مشتريات" and (r[12] or 0):
                pur.setdefault((_d(r[1]), _t(r[3]), r[4]), []).append(r)
            elif _t(r[6]) == "خصم مكتسب" and (r[13] or 0):
                self.docs.append((_d(r[1]), 0, len(self.docs), "pur_disc", r))
        for k, rows in pur.items():
            self.docs.append((k[0], 0, len(self.docs), "purchase", rows))
        moves = collections.OrderedDict()
        for r in d["مخازن"][3:629]:
            if not r[1] or not _t(r[6]):
                continue
            moves.setdefault((_d(r[1]), _t(r[6]), r[2]), []).append(r)
        for k, rows in moves.items():
            self.docs.append((k[0], 2, len(self.docs), "move", rows))
        self.docs.sort(key=lambda x: (x[0], x[1], x[2]))

    # ---------------------------------------------------------------- stock helpers
    def wac(self, it):
        q = self.qty[it["name"]]
        if q > 1e-9 and self.val[it["name"]] > 0:
            return self.val[it["name"]] / q
        return it.get("last_cost") or it.get("cost") or 0.0

    def stock_in(self, it, q, v):
        self.qty[it["name"]] += q
        self.val[it["name"]] += v
        if q:
            it["last_cost"] = v / q

    def stock_out(self, it, q):
        cost = self.wac(it)
        v = round(q * cost, 2)
        self.qty[it["name"]] -= q
        self.val[it["name"]] -= v
        if abs(self.qty[it["name"]]) < 1e-9:
            self.val[it["name"]] = 0.0
        return cost, v

    # ---------------------------------------------------------------- build
    def build(self, group_acc):
        """Returns list of documents: dict(kind, date, desc, lines=[(code, party, dr, cr, note)], stock=[...],
        sales=[...], ref)."""
        self.group_acc = group_acc
        for it in self.items.values():
            self.stock_in(it, it["qty"], round(it["qty"] * it["cost"], 2))
        self.collect()
        out = []
        for dt, _, _, kind, payload in self.docs:
            doc = getattr(self, "_" + kind)(dt, payload)
            if doc:
                out.append(doc)
        return out

    def _cash(self, dt, r):
        acc = CASH_ACC.get(_t(r[11]) or None, "1501")
        counter = CASH_COUNTER[_t(r[2])]
        amt_in, amt_out = float(r[3] or 0), float(r[4] or 0)
        party = ""
        if counter == "1401":
            party = self.party(r[6], "عميل نقدى")
        elif counter == "2101":
            party = self.party(r[6], "عمر غازولى" if "غازولى" in _t(r[5]) else "مورد نقدى")
        desc = _t(r[5]) or _t(r[2])
        note = " - ".join(x for x in (_t(r[6]) if not party else "", _t(r[7]) if isinstance(r[7], str) else "") if x)
        if amt_in:
            lines = [(acc, "", amt_in, 0, ""), (counter, party, 0, amt_in, note)]
        else:
            lines = [(counter, party, amt_out, 0, note), (acc, "", 0, amt_out, "")]
        return {"kind": "cash", "date": dt, "desc": f"خزينة {r[0]}: {desc}", "lines": lines}

    def _sale(self, dt, rows):
        r0 = rows[0]
        party = self.party(r0[4], "عميل نقدى")
        total = round(sum(float(r[11] or 0) for r in rows), 2)
        sales = [(_t(r[7]), _t(r[5]), _t(r[8]), float(r[9] or 0), float(r[10] or 0), float(r[11] or 0)) for r in rows]
        return {"kind": "sale", "date": dt, "desc": f"فاتورة بيع - إذن {r0[3]} - {party}", "dispatch": r0[3],
                "party": party, "sales": sales,
                "lines": [("1401", party, total, 0, f"إذن {r0[3]}"), ("4101", party, 0, total, f"إذن {r0[3]}")]}

    def _sale_adj(self, dt, r):
        party = self.party(r[4], "عميل نقدى")
        kind = _t(r[6])
        if kind == "مرتجع مبيعات":
            v = abs(float(r[11] or 0))
            sales = [(_t(r[7]), _t(r[5]), _t(r[8]), abs(float(r[9] or 0)), float(r[10] or 0), v)]
            return {"kind": "return", "date": dt, "desc": f"مرتجع مبيعات - إذن {r[3]} - {party}", "dispatch": r[3],
                    "party": party, "sales": sales,
                    "lines": [("4201", party, v, 0, _t(r[7])), ("1401", party, 0, v, _t(r[7]))]}
        if kind == "خصم مسموح به":
            v = float(r[12] or 0)
            return {"kind": "jv", "date": dt, "desc": f"خصم مسموح به - إذن {r[3]} - {party}",
                    "lines": [("4202", party, v, 0, _t(r[7])), ("1401", party, 0, v, _t(r[7]))]}
        v = float(r[11] or 0) - float(r[12] or 0)   # other income charged to the customer
        lines = [("1401", party, v, 0, _t(r[7])), ("4304", party, 0, v, _t(r[7]))] if v > 0 else \
            [("4304", party, -v, 0, _t(r[7])), ("1401", party, 0, -v, _t(r[7]))]
        return {"kind": "jv", "date": dt, "desc": f"إيرادات أخرى - {party}", "lines": lines}

    def _pur_disc(self, dt, r):
        party = self.party(r[3], "مورد نقدى")
        v = float(r[13])
        note = _t(r[15]) or _t(r[4])
        lines = [("2101", party, v, 0, note), ("4302", party, 0, v, note)] if v > 0 else \
            [("4302", party, -v, 0, note), ("2101", party, 0, -v, note)]
        return {"kind": "jv", "date": dt, "desc": f"خصم مكتسب - {party}", "lines": lines}

    def _purchase(self, dt, rows):
        r0 = rows[0]
        party = self.party(r0[3], "مورد نقدى")
        by_acc, stock, assets = collections.defaultdict(float), [], []
        for r in rows:
            store, total, q = _t(r[7]), float(r[12] or 0), float(r[10] or 0)
            if store == "أجهزة كهربائية":
                by_acc["1103"] += total
                assets.append((_t(r[8]), "1103", total))
                continue
            it = self.item_for(r[5], r[8], store, _t(r[9]))
            if it is None:
                self.issues.append(f"مشتريات {dt} بند «{_t(r[8])}» مخزن «{store}» مش معروف - اتسجل مصروفات عامة")
                by_acc["6114"] += total
                continue
            self.stock_in(it, q, total)
            by_acc[it["account"]] += total
            stock.append(("PUR", it, q, 0, total / q if q else 0, total, 0, party, "", _t(r[8])))
        total = round(sum(by_acc.values()), 2)
        lines = [(a, "", round(v, 2), 0, "") for a, v in by_acc.items()] + [("2101", party, 0, total, "")]
        ref = f" - إذن {r0[4]}" if r0[4] else ""
        return {"kind": "purchase", "date": dt, "desc": f"فاتورة شراء{ref} - {party}", "lines": lines,
                "stock": stock, "assets": assets, "party": party}

    def _move(self, dt, rows):
        kind = _t(rows[0][6])
        if kind == "تحويلات داخلية":
            return self._compound(dt, rows)
        if kind == "تسويات جردية":
            return self._count(dt, rows)
        party = self.party(rows[0][3], "") if _t(rows[0][3]) else ""
        by_acc, stock = collections.defaultdict(float), []
        for r in rows:
            q = float(r[14] or 0) - float(r[12] or 0)
            if abs(q) < 1e-9:
                continue
            it = self.item_for(r[7], r[8], _t(r[18]), _t(r[9]))
            if it is None:
                self.issues.append(f"صرف {dt} إذن {r[2]} كود «{_t(r[7])}» مش معرف - اتساب (قيمته في الشيت {r[15] or 0})")
                continue
            if q < 0:
                cost = self.wac(it)
                v = round(-q * cost, 2)
                self.stock_in(it, -q, v)
                by_acc[it["account"]] -= v
                stock.append(("ISS", it, -q, 0, cost, v, 0, party, _t(r[4]), "مرتجع للمخزن"))
            else:
                cost, v = self.stock_out(it, q)
                by_acc[it["account"]] += v
                stock.append(("ISS", it, 0, q, cost, 0, v, party, _t(r[4]), ""))
        if not stock:
            return None
        total = round(sum(by_acc.values()), 2)
        lines = [("5101", party, total, 0, "")] + [(a, "", 0, round(v, 2), "") for a, v in by_acc.items()]
        return {"kind": "issue", "date": dt, "desc": f"صرف خامات لأوردرات - إذن {rows[0][2]}" +
                (f" - {party}" if party else ""), "lines": lines, "stock": stock}

    def _compound(self, dt, rows):
        outs, ins, stock = [], [], []
        for r in rows:
            q_in, q_out = float(r[12] or 0), float(r[14] or 0)
            it = self.item_for(r[7], r[8], _t(r[18]), _t(r[9]))
            if it is None:
                continue
            if q_out:
                outs.append((it, q_out))
            if q_in:
                ins.append((it, q_in))
        by_acc, total = collections.defaultdict(float), 0.0
        for it, q in outs:
            cost, v = self.stock_out(it, q)
            total += v
            by_acc[it["account"]] -= v
            stock.append(("CMP", it, 0, q, cost, 0, v, "", "تضريبة", "خامات منصرفة للتضريبة"))
        qin = sum(q for _, q in ins)
        for it, q in ins:
            v = round(total * q / qin, 2)
            self.stock_in(it, q, v)
            by_acc[it["account"]] += v
            stock.append(("CMP", it, q, 0, v / q, v, 0, "", "تضريبة", "تضريبة واردة"))
        lines = [(a, "", round(v, 2), 0, "") for a, v in by_acc.items() if v > 0.004] + \
                [(a, "", 0, round(-v, 2), "") for a, v in by_acc.items() if v < -0.004]
        if not lines:   # same account on both sides: show the transfer
            lines = [("1301", "", round(total, 2), 0, "تضريبة واردة"), ("1301", "", 0, round(total, 2), "خامات منصرفة")]
        return {"kind": "compound", "date": dt, "desc": f"تضريبة - إذن {rows[0][2]}", "lines": lines, "stock": stock}

    def _count(self, dt, rows):
        gain = loss = 0.0
        by_acc, stock = collections.defaultdict(float), []
        net = collections.OrderedDict()
        for r in rows:
            q = float(r[12] or 0) - float(r[14] or 0)
            if abs(q) < 1e-9:
                continue
            it = self.item_for(r[7], r[8], _t(r[18]), _t(r[9]))
            if it is None:
                self.issues.append(f"تسوية جرد {dt} كود «{_t(r[7])}» مخزن «{_t(r[18])}» مش مخزني - اتسابت")
                continue
            net[it["name"]] = (it, net.get(it["name"], (it, 0))[1] + q)
        for it, q in net.values():
            if abs(q) < 1e-9:
                continue
            if q > 0:
                cost = self.wac(it)
                v = round(q * cost, 2)
                self.stock_in(it, q, v)
                gain += v
                by_acc[it["account"]] += v
                stock.append(("ADJ+", it, q, 0, cost, v, 0, "", "", "زيادة جرد"))
            else:
                cost, v = self.stock_out(it, -q)
                loss += v
                by_acc[it["account"]] -= v
                stock.append(("ADJ-", it, 0, -q, cost, 0, v, "", "", "عجز جرد"))
        if not stock:
            return None
        lines = [(a, "", round(v, 2), 0, "") for a, v in by_acc.items() if v > 0.004] + \
                [(a, "", 0, round(-v, 2), "") for a, v in by_acc.items() if v < -0.004]
        if gain:
            lines.append(("4303", "", 0, round(gain, 2), "أرباح فروق جرد"))
        if loss:
            lines.append(("5403", "", round(loss, 2), 0, "عجز فروق جرد"))
        dr, cr = sum(l[2] for l in lines), sum(l[3] for l in lines)
        if round(dr - cr, 2):
            lines.append(("5403", "", round(cr - dr, 2), 0, "تقريب") if cr > dr else ("4303", "", 0, round(dr - cr, 2), "تقريب"))
        return {"kind": "count", "date": dt, "desc": f"تسوية جرد - {dt:%Y-%m-%d}", "lines": lines, "stock": stock}

    # ---------------------------------------------------------------- molds and orders
    def molds_and_usage(self):
        oc = [r for r in self.d["التكاليف والاوردات"][2:1268] if r[1] and _t(r[5])]
        molds, usage = collections.OrderedDict(), []
        for r in oc:
            name, cust = _t(r[5]), self.party(r[3], "")
            m = molds.setdefault(name, {"name": name, "customer": cust, "unit": "جوز" if _t(r[7]) == "جوز" else "قطعة",
                                        "first": _d(r[1])})
            usage.append((_d(r[1]), name, _t(r[4]), cust, float(r[8] or 0), 1, f"إذن {r[2]} - من شيت التكاليف"))
        return list(molds.values()), usage
