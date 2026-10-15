"""Cost card per piece for a saved quote (any revision), with Excel and PDF export."""
import streamlit as st

from core.models.costing import METHODS, QUOTE_STATUSES
from core.services import quotes
from ui.common import export_buttons, guard, page_header

SCREEN = "cost_card"
PRICE = st.column_config.NumberColumn(format="%.4f")


def subtitle(d: dict) -> str:
    party = f" — العميل: {d['party_name']}" if d.get("party_name") else ""
    return (f"{d['doc_no']} — {d['quote_date']} — {d['item_name']}{party} — الكمية {d['qty']:g} {d['unit']} — "
            f"الطريقة المعتمدة: {METHODS[d['method']]}")


def show_card(d: dict, screen_key: str) -> None:
    """The cost card of one quote revision, the chosen method's headline figures, and export."""
    res = quotes.calc(d)[d["method"]]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("التكلفة الكلية للقطعة", f"{res['total']:,.4f}")
    c2.metric("السعر المقترح للقطعة", f"{res['price_piece']:,.4f}")
    c3.metric(f"السعر المقترح لل{d['unit']}", f"{res['price_unit']:,.2f}")
    c4.metric("السعر المقترح للكيلو", f"{res['price_kg']:,.2f}" if res["price_kg"] else "—")
    if d.get("offered_price"):
        st.info(f"السعر المعروض على العميل: {d['offered_price']:,.2f} لل{d['unit']} — "
                f"هامش فعلي {res['offered_margin_pct']:.2f}% من السعر")
    card = quotes.cost_card(d)
    st.dataframe(card, hide_index=True, width="stretch", height=(len(card) + 1) * 35 + 3,
                 column_config={m: PRICE for m in METHODS.values()})
    export_buttons(card, "كارتة تكلفة القطعة", screen_key, f"cost_card_{d['doc_no']}", subtitle=subtitle(d))


def render() -> None:
    guard(SCREEN)
    page_header("كارتة تكلفة القطعة", "من عروض الأسعار المحفوظة؛ كل إصدار بأسعاره ومعدلاته وقت حفظه")
    df = quotes.quotes_frame(current_only=False)
    if df.empty:
        st.info("لا توجد عروض أسعار محفوظة")
        return
    labels = {int(r["id"]): f"{r['رقم العرض']} - {r['المنتج']} - {r['العميل'] or 'بدون عميل'} ({r['الحالة']})"
              for _, r in df.iterrows()}
    qid = st.selectbox("عرض السعر", list(labels), format_func=labels.get, key="cc_pick")
    d = quotes.get_quote(qid)
    if not d["is_current"]:
        st.caption("ده إصدار قديم ومقفول للقراءة.")
    st.caption(f"الحالة: {QUOTE_STATUSES[d['status']]}")
    show_card(d, SCREEN)
