from app.db.base import Base  # re-export for convenience

# Import models so Alembic can discover metadata
from app.models.category import Category  # noqa: F401
from app.models.product import Product  # noqa: F401
from app.models.product_market_data import ProductMarketData  # noqa: F401
from app.models.product_effective_state import ProductEffectiveState  # noqa: F401
from app.models.analysis_run import AnalysisRun  # noqa: F401
from app.models.analysis_run_item import AnalysisRunItem  # noqa: F401
from app.models.setting import Setting  # noqa: F401
from app.models.currency_rate import CurrencyRate  # noqa: F401
