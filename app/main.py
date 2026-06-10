"""FastAPI 应用入口。"""
from __future__ import annotations

from fastapi import FastAPI

from app.config import settings
from app.routers import health, projects, reports, tasks

app = FastAPI(title=settings.app_name, version="1.0.0")

# 健康检查无前缀；业务接口统一前缀 /api/v1
app.include_router(health.router)
app.include_router(projects.router, prefix=settings.api_prefix)
app.include_router(tasks.router, prefix=settings.api_prefix)
app.include_router(reports.router, prefix=settings.api_prefix)


@app.get("/")
async def root() -> dict:
    return {"name": settings.app_name, "docs": "/docs"}
