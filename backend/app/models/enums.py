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


class AnalysisItemSource(str, enum.Enum):
    baza = "baza"
    scraping = "scraping"
    not_found = "not_found"
    error = "error"
