from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings as app_config
from app.models.currency_rate import CurrencyRate
from app.models.setting import Setting

DEFAULT_CURRENCIES = {
    "PLN": (Decimal("1"), True),
    "EUR": (Decimal("4.5"), False),
    "USD": (Decimal("4.2"), False),
    "CAD": (Decimal("3.1"), False),
}


def get_settings(db: Session) -> Setting:
    record = db.query(Setting).first()
    if record:
        return record

    record = Setting(
        cache_ttl_days=app_config.stale_days,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def update_settings(
    db: Session,
    cache_ttl_days: int,
) -> Setting:
    record = get_settings(db)
    # allow 0 to disable cache; cap upper bound for sanity
    record.cache_ttl_days = max(0, min(365, cache_ttl_days))
    db.commit()
    db.refresh(record)
    return record


def _ensure_currency_defaults(db: Session) -> List[CurrencyRate]:
    existing = db.query(CurrencyRate).all()
    if existing:
        return existing
    for code, (rate, is_default) in DEFAULT_CURRENCIES.items():
        db.add(
            CurrencyRate(
                currency=code,
                rate_to_pln=rate,
                is_default=is_default,
            )
        )
    db.commit()
    return db.query(CurrencyRate).all()


def get_currency_rates(db: Session) -> List[CurrencyRate]:
    rates = db.query(CurrencyRate).order_by(CurrencyRate.currency).all()
    if rates:
        return rates
    return _ensure_currency_defaults(db)


def get_currency_rate_map(db: Session) -> Tuple[Dict[str, float], str | None]:
    rates = get_currency_rates(db)
    default_code = None
    mapping: Dict[str, float] = {}
    for r in rates:
        code = r.currency.upper()
        mapping[code] = float(r.rate_to_pln)
        if r.is_default:
            default_code = code
    return mapping, default_code


def update_currency_rates(db: Session, rates: List[dict]) -> List[CurrencyRate]:
    if not rates:
        raise ValueError("Lista kursów nie może być pusta.")

    normalized = []
    default_count = 0
    for entry in rates:
        currency = str(entry.get("currency", "")).upper()
        rate_to_pln = entry.get("rate_to_pln")
        raw_default = entry.get("is_default", False)
        if isinstance(raw_default, str):
            is_default = raw_default.strip().lower() in {"1", "true", "yes", "on"}
        else:
            is_default = bool(raw_default)
        if not currency or len(currency) != 3 or not currency.isalpha():
            raise ValueError(f"Niepoprawny kod waluty: {currency!r}")
        try:
            rate_val = Decimal(str(rate_to_pln))
        except Exception:
            raise ValueError(f"Niepoprawny kurs dla waluty {currency}.")
        if rate_val <= 0:
            raise ValueError(f"Kurs waluty {currency} musi być większy od zera.")
        if is_default:
            default_count += 1
        normalized.append((currency, rate_val, is_default))

    codes = {c for c, _, _ in normalized}
    if "PLN" not in codes:
        raise ValueError("Musi istnieć kurs dla PLN.")

    for cur, rate, _ in normalized:
        if cur == "PLN" and rate != Decimal("1"):
            raise ValueError("Kurs PLN musi wynosić 1.0.")

    if default_count > 1:
        raise ValueError("Wybierz dokładnie jedną walutę domyślną.")

    if default_count == 0:
        pln_idx = next((i for i, (cur, _, _) in enumerate(normalized) if cur == "PLN"), None)
        if pln_idx is None:
            raise ValueError("Brak waluty domyślnej i brak PLN w konfiguracji. Dodaj PLN albo ustaw walutę domyślną.")
        cur, rate, _ = normalized[pln_idx]
        normalized[pln_idx] = (cur, rate, True)

    db.query(CurrencyRate).delete()
    for currency, rate, is_default in normalized:
        db.add(
            CurrencyRate(
                currency=currency,
                rate_to_pln=rate,
                is_default=is_default,
            )
        )
    db.commit()
    return get_currency_rates(db)
