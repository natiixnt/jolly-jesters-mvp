import sqlalchemy as sa
from sqlalchemy import Boolean, Column, Integer, Numeric

from app.db.base import Base


class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, default=1)
    cache_ttl_days = Column(Integer, nullable=False, default=30)

    # -- stop-loss config --
    stoploss_enabled = Column(Boolean, nullable=False, server_default="true", default=True)
    stoploss_window_size = Column(Integer, nullable=False, server_default="20", default=20)
    stoploss_max_error_rate = Column(Numeric(5, 4), nullable=False, server_default="0.5000", default=0.50)
    stoploss_max_captcha_rate = Column(Numeric(5, 4), nullable=False, server_default="0.8000", default=0.80)
    stoploss_max_consecutive_errors = Column(Integer, nullable=False, server_default="10", default=10)
