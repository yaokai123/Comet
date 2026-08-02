"""健康检查：hello 接口 + 四存储连通性探测。"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.core.response import success
from app.db import elastic, neo4j, postgres, redis

router = APIRouter(tags=["health"])


@router.get("/hello")
async def hello():
    """阶段0验证点：前后端跑通的 hello 接口。"""
    return success({"app": settings.app_name, "message": "你好，彗记 Comet"})


@router.get("/health")
async def health():
    """探测四个存储是否连得上。"""
    checks = {
        "postgres": await postgres.ping(),
        "elasticsearch": await elastic.ping(),
        "neo4j": await neo4j.ping(),
        "redis": await redis.ping(),
    }
    all_ok = all(checks.values())
    return success(
        {"healthy": all_ok, "checks": checks},
        message="ok" if all_ok else "部分存储未就绪",
    )


@router.get("/health/ready")
async def ready(request: Request):
    """Readiness probe for Docker/orchestrators; partial startup is not ready."""
    initialized = getattr(request.app.state, "readiness", {})
    live_checks = {
        "postgres": await postgres.ping(),
        "elasticsearch": await elastic.ping(),
        "neo4j": await neo4j.ping(),
        "redis": await redis.ping(),
    }
    checks = {name: bool(initialized.get(name)) and live_checks[name] for name in live_checks}
    ready_now = all(checks.values())
    return JSONResponse(
        status_code=200 if ready_now else 503,
        content=success(
            {"ready": ready_now, "checks": checks},
            message="ready" if ready_now else "dependencies are not ready",
        ),
    )
