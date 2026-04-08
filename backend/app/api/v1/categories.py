import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user_optional
from app.db.session import get_db
from app.schemas.category import CategoryCreate, CategoryRead, CategoryUpdate
from app.services import categories_service

router = APIRouter(tags=["categories"])


@router.get("/", response_model=list[CategoryRead])
def list_categories(include_inactive: bool = False, db: Session = Depends(get_db), current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
    return categories_service.list_categories(db, include_inactive=include_inactive, tenant_id=current_user.tenant_id if current_user else None)


@router.post("/", response_model=CategoryRead, status_code=status.HTTP_201_CREATED)
def create_category(payload: CategoryCreate, db: Session = Depends(get_db), current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
    try:
        return categories_service.create_category(db, payload, tenant_id=current_user.tenant_id if current_user else None)
    except IntegrityError:
        raise HTTPException(status_code=400, detail="Kategoria o tej nazwie juz istnieje")


@router.patch("/{category_id}", response_model=CategoryRead)
def update_category(category_id: str, payload: CategoryUpdate, db: Session = Depends(get_db), current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
    try:
        category_uuid = uuid.UUID(category_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Nieprawidlowy identyfikator kategorii")

    try:
        category = categories_service.update_category(db, str(category_uuid), payload)
    except IntegrityError:
        raise HTTPException(status_code=400, detail="Kategoria o tej nazwie juz istnieje")

    if not category:
        raise HTTPException(status_code=404, detail="Nie znaleziono kategorii")
    return category


@router.get("/{category_id}", response_model=CategoryRead)
def get_category(category_id: str, db: Session = Depends(get_db), current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
    try:
        category_uuid = uuid.UUID(category_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Nieprawidlowy identyfikator kategorii")

    category = categories_service.get_category(db, str(category_uuid))
    if not category:
        raise HTTPException(status_code=404, detail="Nie znaleziono kategorii")
    return category
