from sqlalchemy import Boolean, Column, Numeric, String

from app.db.base import Base


class CurrencyRate(Base):
    __tablename__ = "currency_rates"

    currency = Column(String(8), primary_key=True)
    rate_to_pln = Column(Numeric(12, 6), nullable=False)
    is_default = Column(Boolean, nullable=False, default=False, server_default="false")
