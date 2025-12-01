from fastapi import APIRouter

from app.api.v1 import analysis, categories

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(categories.router, prefix="/categories")
api_router.include_router(analysis.router, prefix="/analysis")
