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
