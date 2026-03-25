from fastapi import APIRouter

from app.api.routes.health import router as health_router
from app.api.routes.locations import router as locations_router
from app.api.routes.routes import router as routes_router
from app.api.routes.searches import router as searches_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(locations_router, tags=["locations"])
api_router.include_router(searches_router, tags=["searches"])
api_router.include_router(routes_router, tags=["routes"])
