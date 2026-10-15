"""Rules every approval document follows while it is being written."""
from datetime import datetime

from core.models.documents import APPROVED, PENDING, RETURNED, STATUS_LABELS
from core.services.errors import BusinessError


def own_only(actor, screen_key: str) -> bool:
    return actor.can(screen_key, "own_only")


def check_access(actor, screen_key: str, action: str, doc=None) -> None:
    if not actor.can(screen_key, action):
        raise BusinessError("ليس لديك صلاحية لهذه العملية")
    if doc is not None and own_only(actor, screen_key) and doc.created_by_id != actor.id:
        raise BusinessError("مسموح لك بمستنداتك فقط")


def check_editable(doc) -> None:
    """Approved documents change only after an authorised user un-approves them."""
    if doc.status == APPROVED:
        raise BusinessError(f"{doc.doc_no} معتمد. لازم يتلغى اعتماده الأول من شاشة الاعتمادات.")
    if doc.status not in (PENDING, RETURNED):
        raise BusinessError(f"{doc.doc_no}: حالة غير معروفة")


def resubmit(doc) -> None:
    """Saving a returned document sends it back to the approver; the last note stays for reference."""
    doc.status = PENDING
    doc.updated_at = datetime.now()


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)
