"""Recipes (BOM): what one batch of a finished item or compound is made of."""
import pandas as pd
import streamlit as st

from core.models.costing import LAST, WAC
from core.services import boms as svc, items
from ui.common import export_buttons, guard, page_header, run_action

SCREEN = svc.SCREEN
PRICE = st.column_config.NumberColumn(format="%.4f")


def _list_tab(user) -> None:
    df = svc.boms_frame()
    if df.empty:
        st.info("لا توجد تركيبات. ابدأ من تبويب «تركيبة صنف».")
        return
    st.dataframe(df.drop(columns=["item_id"]), hide_index=True, width="stretch")
    export_buttons(df.drop(columns=["item_id"]), "التركيبات", SCREEN, "boms")


def _lines_editor(key: str, lines: list[dict], comp_opts: dict) -> list[dict]:
    rows = [{"الخامة": comp_opts.get(l["component_id"]), "الكمية": l["qty"], "بالجرام": l["in_grams"]} for l in lines] or \
        [{"الخامة": None, "الكمية": None, "بالجرام": True}] * 3
    df = pd.DataFrame(rows).astype({"الكمية": "float", "بالجرام": "bool"})
    edited = st.data_editor(
        # The editable grid lays out left to right; reverse so the first column sits on the right.
        df[df.columns[::-1]], key=key, num_rows="dynamic", hide_index=True, width="stretch",
        column_config={
            "الخامة": st.column_config.SelectboxColumn(options=list(comp_opts.values()), width="large"),
            "الكمية": st.column_config.NumberColumn(min_value=0.0, format="%.4f",
                                                   help="بالجرام لو العلامة متعلمة، وإلا بوحدة الخامة نفسها"),
            "بالجرام": st.column_config.CheckboxColumn(help="شيل العلامة للأصناف اللي بالعدد زي العلب والاكسسوارات"),
        },
    )
    ids = {v: k for k, v in comp_opts.items()}
    return [{"component_id": ids.get(r["الخامة"]), "qty": r["الكمية"] if pd.notna(r["الكمية"]) else 0,
             "in_grams": bool(r["بالجرام"]) if pd.notna(r["بالجرام"]) else False} for r in edited.to_dict("records")]


def _edit_tab(user) -> None:
    targets = svc.recipe_items()
    if not targets:
        st.warning("سجّل أصناف الإنتاج التام والتضريبة الأول من شاشة الأصناف")
        return
    item_id = st.selectbox("الصنف (✓ = ليه تركيبة)", list(targets), format_func=targets.get, index=None,
                           placeholder="اختار منتج تام أو تضريبة", key="bom_item")
    if item_id is None:
        return
    bom = svc.get_bom(item_id) or {"output_qty": 1.0, "waste_pct": 0.0, "notes": "", "lines": []}
    key = f"bom_{item_id}"
    st.caption("التركيبة بتتكتب لكمية ناتجة (مثلاً قطعة واحدة، أو 100 كيلو تضريبة). الخامات بالكيلو بتتكتب بالجرام.")
    c1, c2 = st.columns(2)
    output = c1.number_input("الكمية الناتجة (قطعة، أو كيلو للتضريبة)", min_value=0.001, value=bom["output_qty"],
                             key=f"{key}_out")
    waste = c2.number_input("الهالك الطبيعي %", min_value=0.0, max_value=99.0, value=bom["waste_pct"], key=f"{key}_waste")
    comps = {k: v for k, v in items.item_options().items() if k != item_id}
    lines = _lines_editor(f"{key}_lines", bom["lines"], comps)
    notes = st.text_input("ملاحظات", value=bom["notes"], key=f"{key}_notes")
    c3, c4, _ = st.columns([1, 1, 3])
    if c3.button("حفظ التركيبة", type="primary", key=f"{key}_save",
                 disabled=not (user.can(SCREEN, "add") or user.can(SCREEN, "edit"))):
        if run_action(svc.save_bom, user, item_id, lines, output, waste, notes, success="تم حفظ التركيبة"):
            st.session_state.pop(f"{key}_lines", None)
            st.rerun()
    if svc.get_bom(item_id) and user.can(SCREEN, "delete") and c4.button("حذف التركيبة", key=f"{key}_del"):
        if run_action(svc.delete_bom, user, item_id, success="تم حذف التركيبة"):
            st.rerun()
    if svc.get_bom(item_id):
        st.divider()
        st.subheader("تكلفة خامات القطعة من التركيبة المحفوظة")
        rows = svc.costed_lines(item_id)
        df = pd.DataFrame([{"الخامة": r["name"], "الكمية للقطعة": r["qty"], "الوحدة": r["unit"],
                            "سعر المتوسط": r[WAC], "مصدره": r["wac_src"], "آخر سعر": r[LAST], "مصدره ": r["last_src"],
                            "قيمة بالمتوسط": r["qty"] * r[WAC], "قيمة بآخر سعر": r["qty"] * r[LAST]} for r in rows])
        st.dataframe(df, hide_index=True, width="stretch",
                     column_config={c: PRICE for c in ("الكمية للقطعة", "سعر المتوسط", "آخر سعر", "قيمة بالمتوسط",
                                                       "قيمة بآخر سعر")})
        c5, c6 = st.columns(2)
        c5.metric("خامات القطعة بالمتوسط", f"{df['قيمة بالمتوسط'].sum():,.4f}")
        c6.metric("خامات القطعة بآخر سعر", f"{df['قيمة بآخر سعر'].sum():,.4f}")
        if (df["مصدره"] == svc.SRC_NONE).any() or (df["مصدره "] == svc.SRC_NONE).any():
            st.warning("فيه خامات مالهاش سعر لسه (مفيش رصيد ولا شراء ولا تركيبة)؛ قيمتها هتبقى صفر لحد ما تتسعّر.")


def render() -> None:
    user = guard(SCREEN)
    page_header("التركيبات (BOM)", "مكونات المنتج التام والتضريبة؛ منها بتتحسب تكلفة الخامات في التسعير")
    tabs = ["التركيبات", "تركيبة صنف"]
    handlers = {"التركيبات": _list_tab, "تركيبة صنف": _edit_tab}
    for tab, name in zip(st.tabs(tabs), tabs):
        with tab:
            handlers[name](user)
