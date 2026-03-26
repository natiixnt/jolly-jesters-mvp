import enum


class ProfitabilityLabel(str, enum.Enum):
    oplacalny = "oplacalny"
    nieoplacalny = "nieoplacalny"
    nieokreslony = "nieokreslony"


class MarketDataSource(str, enum.Enum):
    scraping = "scraping"
    api = "api"


class AnalysisStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    canceled = "canceled"
    stopped = "stopped"


class AnalysisItemSource(str, enum.Enum):
    baza = "baza"
    scraping = "scraping"
    not_found = "not_found"
    error = "error"


class ScrapeStatus(str, enum.Enum):
    pending = "pending"
    in_progress = "in_progress"
    ok = "ok"
    not_found = "not_found"
    blocked = "blocked"
    network_error = "network_error"
    error = "error"


class AlertCondition(str, enum.Enum):
    price_below = "price_below"
    price_above = "price_above"
    price_drop_pct = "price_drop_pct"
    new_seller = "new_seller"
    out_of_stock = "out_of_stock"


class NotificationType(str, enum.Enum):
    alert = "alert"
    run_completed = "run_completed"
    run_stopped = "run_stopped"
    quota_warning = "quota_warning"
    system = "system"
