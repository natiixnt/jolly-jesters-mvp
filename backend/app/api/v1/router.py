from fastapi import APIRouter

from app.api.v1 import analysis, billing, categories, market_data, metrics, proxies, proxy_pool, settings, status, tenants

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
api_router.include_router(status.router)
