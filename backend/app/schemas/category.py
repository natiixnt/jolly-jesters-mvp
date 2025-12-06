from __future__ import annotations

from datetime import datetime
import uuid
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class CategoryBase(BaseModel):
    name: str = Field(..., max_length=255, min_length=1)
    description: Optional[str] = None
    profitability_multiplier: Decimal = Field(default=Decimal("1.5"), ge=0)
    commission_rate: Optional[Decimal] = Field(default=None, ge=0)
    is_active: bool = True


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=255, min_length=1)
    description: Optional[str] = None
    profitability_multiplier: Optional[Decimal] = Field(default=None, ge=0)
    commission_rate: Optional[Decimal] = Field(default=None, ge=0)
    is_active: Optional[bool] = None


class CategoryRead(CategoryBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True
