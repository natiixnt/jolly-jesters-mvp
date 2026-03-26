from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.alert_event import AlertEvent
from app.models.alert_rule import AlertRule
from app.models.notification import Notification
from app.models.product_market_data import ProductMarketData

logger = logging.getLogger(__name__)


def evaluate_rules_for_ean(
    db: Session,
    tenant_id: str,
    ean: str,
    current_price: Optional[Decimal],
    previous_price: Optional[Decimal],
    sold_count: Optional[int],
    is_not_found: bool,
) -> List[AlertEvent]:
    """Check all active rules for a tenant against new scrape data."""
    rules = (
        db.query(AlertRule)
        .filter(
            AlertRule.tenant_id == tenant_id,
            AlertRule.is_active == True,
        )
        .all()
    )

    events = []
    now = datetime.now(timezone.utc)

    for rule in rules:
        # skip if rule is EAN-specific and doesn't match
        if rule.ean and rule.ean != ean:
            continue

        triggered = False
        message = ""

        if rule.condition_type == "price_below" and current_price is not None and rule.threshold_value:
            if current_price < rule.threshold_value:
                triggered = True
                message = f"Cena {ean} spadla do {current_price} PLN (prog: {rule.threshold_value} PLN)"

        elif rule.condition_type == "price_above" and current_price is not None and rule.threshold_value:
            if current_price > rule.threshold_value:
                triggered = True
                message = f"Cena {ean} wzrosla do {current_price} PLN (prog: {rule.threshold_value} PLN)"

        elif rule.condition_type == "price_drop_pct" and current_price and previous_price and rule.threshold_value:
            if previous_price > 0:
                drop_pct = ((previous_price - current_price) / previous_price) * 100
                if drop_pct >= rule.threshold_value:
                    triggered = True
                    message = f"Cena {ean} spadla o {drop_pct:.1f}% ({previous_price} -> {current_price} PLN)"

        elif rule.condition_type == "out_of_stock" and is_not_found:
            triggered = True
            message = f"Produkt {ean} niedostepny na Allegro"

        if triggered:
            event = AlertEvent(
                tenant_id=tenant_id,
                alert_rule_id=rule.id,
                ean=ean,
                condition_type=rule.condition_type,
                message=message,
                details={
                    "current_price": str(current_price) if current_price else None,
                    "previous_price": str(previous_price) if previous_price else None,
                    "threshold": str(rule.threshold_value) if rule.threshold_value else None,
                },
            )
            db.add(event)
            rule.last_triggered_at = now
            events.append(event)

            # create in-app notification
            db.add(Notification(
                tenant_id=tenant_id,
                notification_type="alert",
                title=f"Alert: {rule.name}",
                message=message,
                channel="in_app",
            ))

    if events:
        db.commit()

    return events


def create_rule(
    db: Session,
    tenant_id: str,
    name: str,
    condition_type: str,
    threshold_value: Optional[Decimal] = None,
    ean: Optional[str] = None,
    category_id: Optional[str] = None,
    notify_email: bool = True,
    notify_webhook: bool = False,
    webhook_url: Optional[str] = None,
) -> AlertRule:
    rule = AlertRule(
        tenant_id=tenant_id,
        name=name,
        condition_type=condition_type,
        threshold_value=threshold_value,
        ean=ean,
        category_id=category_id,
        notify_email=notify_email,
        notify_webhook=notify_webhook,
        webhook_url=webhook_url,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def list_rules(db: Session, tenant_id: str, active_only: bool = True) -> List[AlertRule]:
    q = db.query(AlertRule).filter(AlertRule.tenant_id == tenant_id)
    if active_only:
        q = q.filter(AlertRule.is_active == True)
    return q.order_by(AlertRule.created_at.desc()).all()


def list_events(
    db: Session, tenant_id: str, limit: int = 50
) -> List[AlertEvent]:
    return (
        db.query(AlertEvent)
        .filter(AlertEvent.tenant_id == tenant_id)
        .order_by(AlertEvent.created_at.desc())
        .limit(limit)
        .all()
    )


def delete_rule(db: Session, tenant_id: str, rule_id: int) -> bool:
    rule = (
        db.query(AlertRule)
        .filter(AlertRule.id == rule_id, AlertRule.tenant_id == tenant_id)
        .first()
    )
    if not rule:
        return False
    rule.is_active = False
    db.commit()
    return True


def get_previous_price(db: Session, ean: str) -> Optional[Decimal]:
    """Get the previous known price for an EAN from market data history."""
    rows = (
        db.query(ProductMarketData.allegro_price)
        .join(ProductMarketData.product)
        .filter(
            ProductMarketData.allegro_price.isnot(None),
            ProductMarketData.is_not_found == False,
        )
        .order_by(ProductMarketData.fetched_at.desc())
        .limit(2)
        .all()
    )
    if len(rows) >= 2:
        return rows[1][0]
    return None
