"""Chart of accounts: seeding, browsing, maintenance and Excel import."""
import pandas as pd
from sqlalchemy import select

from core.db import session_scope
from core.models import Account
from core.models.accounting import ACCOUNT_TYPES, NORMAL_SIDE, PL_SECTIONS, STATEMENTS
from core.seed.coa import ACCOUNTS, CLASSES, GROUP_SECTIONS
from core.services import audit
from core.services.errors import BusinessError

# Column headers of the import template, in order.
IMPORT_COLUMNS = ["كود الحساب", "اسم الحساب", "الاسم بالإنجليزية", "كود الحساب الأب"]


def level_for_code(code: str) -> int:
    return {1: 1, 2: 2, 4: 3, 6: 4}.get(len(code), 0)


def parent_code(code: str) -> str | None:
    if len(code) <= 1:
        return None
    return code[:1] if len(code) == 2 else code[:-2]


def _classify(code: str) -> tuple[str, str, str | None]:
    account_type, statement = CLASSES[code[0]]
    section = GROUP_SECTIONS.get(code[:2]) if statement == "PL" and len(code) >= 2 else None
    return account_type, statement, section


def _normal_side(account_type: str, is_contra: bool) -> str:
    side = NORMAL_SIDE[account_type]
    if is_contra:
        side = "credit" if side == "debit" else "debit"
    return side


def seed_chart(session) -> int:
    """Insert any default account that does not exist yet. Returns the number added."""
    by_code = {a.code: a for a in session.scalars(select(Account))}
    codes_with_children = {parent_code(c) for c, *_ in ACCOUNTS}
    added = 0
    for code, name_ar, name_en, system_key, is_contra in ACCOUNTS:
        if code in by_code:
            continue
        account_type, statement, section = _classify(code)
        parent = by_code.get(parent_code(code)) if parent_code(code) else None
        acc = Account(
            code=code,
            name_ar=name_ar,
            name_en=name_en,
            parent=parent,
            level=level_for_code(code),
            account_type=account_type,
            normal_side=_normal_side(account_type, is_contra),
            statement=statement,
            pl_section=section,
            is_postable=code not in codes_with_children,
            is_contra=is_contra,
            is_system=system_key is not None,
            system_key=system_key,
        )
        session.add(acc)
        session.flush()
        by_code[code] = acc
        added += 1
    return added


def accounts_frame(include_inactive: bool = True) -> pd.DataFrame:
    """Accounts in tree order with display columns."""
    with session_scope() as s:
        accounts = s.scalars(select(Account).order_by(Account.code)).all()
        rows = []
        for a in accounts:
            if not include_inactive and not a.is_active:
                continue
            rows.append(
                {
                    "id": a.id,
                    "الكود": a.code,
                    "اسم الحساب": a.name_ar,
                    "الاسم بالإنجليزية": a.name_en or "",
                    "المستوى": a.level,
                    "النوع": ACCOUNT_TYPES[a.account_type],
                    "الطبيعة": "مدين" if a.normal_side == "debit" else "دائن",
                    "القائمة": STATEMENTS[a.statement],
                    "قسم قائمة الدخل": PL_SECTIONS.get(a.pl_section, "") if a.pl_section else "",
                    "حساب فرعي": a.is_postable,
                    "حساب مقابل": a.is_contra,
                    "نظامي": a.is_system,
                    "نشط": a.is_active,
                }
            )
    return pd.DataFrame(rows)


def tree_label(code: str, name: str, level: int) -> str:
    return f"{'　' * (level - 1)}{code} - {name}"


def next_child_code(parent_id: int) -> str:
    with session_scope() as s:
        parent = s.get(Account, parent_id)
        if parent is None:
            raise BusinessError("الحساب الأب غير موجود")
        return _next_child_code(s, parent)


def _next_child_code(session, parent: Account) -> str:
    width = 1 if parent.level == 1 else 2
    if parent.level >= 4:
        raise BusinessError("أقصى عمق للشجرة 4 مستويات")
    used = {c.code for c in parent.children}
    for n in range(1, 10 ** width):
        code = f"{parent.code}{n:0{width}d}"
        if code not in used and session.scalar(select(Account.id).where(Account.code == code)) is None:
            return code
    raise BusinessError("لا توجد أكواد متاحة تحت هذا الحساب")


def create_account(actor, parent_id: int, name_ar: str, name_en: str | None = None, code: str | None = None) -> int:
    name_ar = (name_ar or "").strip()
    if not name_ar:
        raise BusinessError("اسم الحساب مطلوب")
    with session_scope() as s:
        parent = s.get(Account, parent_id)
        if parent is None:
            raise BusinessError("الحساب الأب غير موجود")
        code = (code or "").strip() or _next_child_code(s, parent)
        if parent_code(code) != parent.code or level_for_code(code) != parent.level + 1:
            raise BusinessError(f"الكود {code} لا يتبع الحساب الأب {parent.code}")
        if s.scalar(select(Account.id).where(Account.code == code)):
            raise BusinessError(f"الكود {code} مستخدم")
        if parent.is_postable:
            # A leaf that gets a child becomes a header; it must not carry postings then.
            # TODO(phase 1): refuse when the parent already has journal lines.
            parent.is_postable = False
        acc = Account(
            code=code, name_ar=name_ar, name_en=(name_en or "").strip() or None, parent=parent,
            level=parent.level + 1, account_type=parent.account_type, normal_side=parent.normal_side,
            statement=parent.statement, pl_section=parent.pl_section or _classify(code)[2],
            is_postable=True, is_contra=parent.is_contra,
        )
        s.add(acc)
        s.flush()
        audit.log(s, actor, "create", "account", acc.id, {"code": code, "name": name_ar})
        return acc.id


def update_account(actor, account_id: int, name_ar: str, name_en: str | None, is_active: bool) -> None:
    name_ar = (name_ar or "").strip()
    if not name_ar:
        raise BusinessError("اسم الحساب مطلوب")
    with session_scope() as s:
        acc = s.get(Account, account_id)
        if acc is None:
            raise BusinessError("الحساب غير موجود")
        if acc.is_system and not is_active:
            raise BusinessError("لا يمكن إيقاف حساب يستخدمه النظام في القيود الآلية")
        before = {"name_ar": acc.name_ar, "name_en": acc.name_en, "is_active": acc.is_active}
        acc.name_ar, acc.name_en, acc.is_active = name_ar, (name_en or "").strip() or None, is_active
        audit.log(s, actor, "update", "account", acc.id, {"before": before, "code": acc.code})


def delete_account(actor, account_id: int) -> None:
    with session_scope() as s:
        acc = s.get(Account, account_id)
        if acc is None:
            return
        if acc.is_system:
            raise BusinessError("لا يمكن حذف حساب نظامي")
        if acc.children:
            raise BusinessError("لا يمكن حذف حساب له حسابات فرعية")
        # TODO(phase 1): refuse when the account has journal lines.
        parent = acc.parent
        audit.log(s, actor, "delete", "account", acc.id, {"code": acc.code, "name": acc.name_ar})
        s.delete(acc)
        s.flush()
        if parent is not None and not parent.children:
            parent.is_postable = True


# ---------- Excel import ----------

def import_template() -> pd.DataFrame:
    return pd.DataFrame(
        [["610115", "مصروفات نظافة", "Cleaning", "6101"]], columns=IMPORT_COLUMNS
    ).iloc[0:0]


def validate_import(df: pd.DataFrame) -> pd.DataFrame:
    """Return the rows with a 'الحالة' column: 'جاهز' or the reason the row will be skipped."""
    missing = [c for c in IMPORT_COLUMNS[:2] + [IMPORT_COLUMNS[3]] if c not in df.columns]
    if missing:
        raise BusinessError("أعمدة ناقصة في الملف: " + "، ".join(missing))
    df = df.copy()
    for col in IMPORT_COLUMNS:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("").astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    with session_scope() as s:
        existing = set(s.scalars(select(Account.code)))
    pending = set()
    status = []
    for rec in df.to_dict("records"):
        code, name, parent = rec[IMPORT_COLUMNS[0]], rec[IMPORT_COLUMNS[1]], rec[IMPORT_COLUMNS[3]]
        if not code or not name:
            status.append("الكود والاسم مطلوبين")
        elif not code.isdigit() or level_for_code(code) < 2:
            status.append("الكود لازم يكون أرقام بطول 2 أو 4 أو 6")
        elif code in existing or code in pending:
            status.append("الكود موجود")
        elif parent_code(code) != parent:
            status.append(f"الكود الأب لازم يكون {parent_code(code)}")
        elif parent not in existing and parent not in pending:
            status.append("الحساب الأب غير موجود")
        else:
            status.append("جاهز")
            pending.add(code)
    df["الحالة"] = status
    return df


def import_accounts(actor, df: pd.DataFrame) -> int:
    checked = validate_import(df)
    ready = checked[checked["الحالة"] == "جاهز"].sort_values(IMPORT_COLUMNS[0], key=lambda c: c.str.len())
    count = 0
    for rec in ready.to_dict("records"):
        with session_scope() as s:
            parent_id = s.scalar(select(Account.id).where(Account.code == rec[IMPORT_COLUMNS[3]]))
        create_account(actor, parent_id, rec[IMPORT_COLUMNS[1]], rec[IMPORT_COLUMNS[2]], rec[IMPORT_COLUMNS[0]])
        count += 1
    return count
