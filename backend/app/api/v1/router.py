from fastapi import APIRouter

from app.api.v1 import (
    alerts,
    analysis,
    api_keys,
    billing,
    categories,
    market_data,
    metrics,
    monitoring,
    notifications,
    price_history,
    proxies,
    proxy_pool,
    settings,
    status,
    tenants,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(categories.router, prefix="/categories")
api_router.include_router(analysis.router, prefix="/analysis")
api_router.include_router(settings.router, prefix="/settings")
api_router.include_router(market_data.router, prefix="/market-data")
api_router.include_router(proxies.router, prefix="/proxies")
api_router.include_router(proxy_pool.router, prefix="/proxy-pool")
api_router.include_router(tenants.router, prefix="/tenants")
api_router.include_router(billing.router, prefix="/billing")
api_router.include_router(metrics.router, prefix="/metrics")
api_router.include_router(monitoring.router, prefix="/monitoring")
api_router.include_router(alerts.router, prefix="/alerts")
api_router.include_router(notifications.router, prefix="/notifications")
api_router.include_router(api_keys.router, prefix="/api-keys")
api_router.include_router(price_history.router, prefix="/price-history")
api_router.include_router(status.router)
