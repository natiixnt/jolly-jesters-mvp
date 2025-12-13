from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.category import Category
from app.schemas.category import CategoryCreate, CategoryUpdate


def list_categories(db: Session, include_inactive: bool = False) -> list[Category]:
    query = db.query(Category)
    if not include_inactive:
        query = query.filter(Category.is_active.is_(True))
    return query.order_by(Category.name).all()


def create_category(db: Session, payload: CategoryCreate) -> Category:
    category = Category(
        name=payload.name,
        description=payload.description,
        profitability_multiplier=payload.profitability_multiplier,
        commission_rate=payload.commission_rate,
        is_active=payload.is_active,
    )
    db.add(category)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise
    db.refresh(category)
    return category


def get_category(db: Session, category_id: str) -> Category | None:
    return db.query(Category).filter(Category.id == category_id).first()


def update_category(db: Session, category_id: str, payload: CategoryUpdate) -> Category:
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        return None

    for field, value in payload.dict(exclude_unset=True).items():
        setattr(category, field, value)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise
    db.refresh(category)
    return category
