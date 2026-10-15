"""Import every model module so Base.metadata knows all tables."""
from core.models.security import AuditLog, Role, RolePermission, User  # noqa: F401
from core.models.settings import CompanySettings  # noqa: F401
from core.models.accounting import Account, FiscalPeriod  # noqa: F401
