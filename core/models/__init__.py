"""Import every model module so Base.metadata knows all tables."""
from core.models.security import AuditLog, Role, RolePermission, User  # noqa: F401
from core.models.settings import CompanySettings  # noqa: F401
from core.models.accounting import Account, FiscalPeriod  # noqa: F401
from core.models.parties import Party  # noqa: F401
from core.models.ledger import JournalEntry, JournalLine  # noqa: F401
from core.models.documents import CashVoucher, ManualJournal, ManualJournalLine  # noqa: F401
from core.models.notifications import Notification  # noqa: F401
from core.models.inventory import (  # noqa: F401
    Item, ItemGroup, PurchaseInvoice, PurchaseLine, StockMove, StockVoucher, StockVoucherLine,
)
from core.models.costing import Bom, BomLine, CostRate, PriceQuote, PriceQuoteLine  # noqa: F401
from core.models.molds import (  # noqa: F401
    Mold, MoldAmortization, MoldAmortizationLine, MoldMaintenance, MoldSettlement, MoldUsage,
)
