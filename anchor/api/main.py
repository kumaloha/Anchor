from contextlib import asynccontextmanager

from fastapi import FastAPI

from anchor.api.routers import collector as collector_router
from anchor.database.session import create_tables


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_tables()
    yield


app = FastAPI(
    title="Anchor API",
    description="经济观点准确性追踪系统",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(collector_router.router, prefix="/collector", tags=["采集层"])