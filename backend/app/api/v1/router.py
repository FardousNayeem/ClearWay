from fastapi import APIRouter

from . import air, models, stations

api_router = APIRouter()
api_router.include_router(stations.router)
api_router.include_router(air.router)
api_router.include_router(models.router)
