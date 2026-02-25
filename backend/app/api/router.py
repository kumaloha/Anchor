from fastapi import APIRouter

from app.api.endpoints import bloggers, ingest, opinions, tracking

api_router = APIRouter(prefix="/api")

api_router.include_router(bloggers.router)
api_router.include_router(opinions.router)
api_router.include_router(ingest.router)
api_router.include_router(tracking.router)
