import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.category import CategoryCreate, CategoryRead, CategoryUpdate
from app.services import categories_service

router = APIRouter(tags=["categories"])


@router.get("/", response_model=list[CategoryRead])
def list_categories(include_inactive: bool = False, db: Session = Depends(get_db)):
    return categories_service.list_categories(db, include_inactive=include_inactive)


@router.post("/", response_model=CategoryRead, status_code=status.HTTP_201_CREATED)
def create_category(payload: CategoryCreate, db: Session = Depends(get_db)):
    try:
        return categories_service.create_category(db, payload)
    except IntegrityError:
        raise HTTPException(status_code=400, detail="Category with this name already exists")


@router.patch("/{category_id}", response_model=CategoryRead)
def update_category(category_id: str, payload: CategoryUpdate, db: Session = Depends(get_db)):
    try:
        category_uuid = uuid.UUID(category_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid category id")

    try:
        category = categories_service.update_category(db, str(category_uuid), payload)
    except IntegrityError:
        raise HTTPException(status_code=400, detail="Category with this name already exists")

    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    return category
