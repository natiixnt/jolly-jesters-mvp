from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class CategoryBase(BaseModel):
    name: str = Field(..., max_length=255)
    description: Optional[str] = None
    profitability_multiplier: Decimal = Field(default=Decimal("1.5"))
    commission_rate: Optional[Decimal] = None
    is_active: bool = True


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = None
    profitability_multiplier: Optional[Decimal] = None
    commission_rate: Optional[Decimal] = None
    is_active: Optional[bool] = None


class CategoryOut(CategoryBase):
    id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True
