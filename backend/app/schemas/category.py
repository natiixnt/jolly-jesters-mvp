from __future__ import annotations

from datetime import datetime
import uuid
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, validator

from app.utils.validators import sanitize_string


class CategoryBase(BaseModel):
    name: str = Field(..., max_length=255, min_length=1)
    description: Optional[str] = Field(None, max_length=2000)
    profitability_multiplier: Decimal = Field(default=Decimal("1.5"), ge=0, le=100)
    commission_rate: Optional[Decimal] = Field(default=None, ge=0, le=1)
    is_active: bool = True

    @validator("name")
    def sanitize_name(cls, v):
        return sanitize_string(v, max_length=255)

    @validator("description", pre=True)
    def sanitize_description(cls, v):
        if v is None:
            return v
        return sanitize_string(v, max_length=2000)


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=255, min_length=1)
    description: Optional[str] = Field(None, max_length=2000)
    profitability_multiplier: Optional[Decimal] = Field(default=None, ge=0, le=100)
    commission_rate: Optional[Decimal] = Field(default=None, ge=0, le=1)
    is_active: Optional[bool] = None

    @validator("name", pre=True)
    def sanitize_name(cls, v):
        if v is None:
            return v
        return sanitize_string(v, max_length=255)

    @validator("description", pre=True)
    def sanitize_description(cls, v):
        if v is None:
            return v
        return sanitize_string(v, max_length=2000)


class CategoryRead(CategoryBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True
