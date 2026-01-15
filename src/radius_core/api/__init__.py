"""API модуль."""

from .endpoints import router
from .login_analyzer import router as analyzer_router

# Объединяем все роутеры
from fastapi import APIRouter

main_router = APIRouter()
main_router.include_router(router)
main_router.include_router(analyzer_router, prefix="/analyzer", tags=["analyzer"])

__all__ = ["router"]
