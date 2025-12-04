from fastapi import APIRouter

from app.api.v1 import analysis, categories, settings

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(categories.router, prefix="/categories")
api_router.include_router(analysis.router, prefix="/analysis")
api_router.include_router(settings.router, prefix="/settings")
