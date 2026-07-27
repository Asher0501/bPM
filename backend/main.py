# -*- coding: utf-8 -*-
"""bePm — AI 辅助项目排期管理系统 · 后端入口"""

import logging
import os
import signal
import sys
import io
import time
from pathlib import Path
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from config import init_env

# ═══════════════════════════════════════════════════════════════════════
# Windows 编码修复
# ═══════════════════════════════════════════════════════════════════════

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 初始化环境变量
init_env()

# 加载项目配置
from config import get_config as _get_config
_app_config = _get_config()

# ═══════════════════════════════════════════════════════════════════════
# 日志系统：控制台 + 文件轮转
# ═══════════════════════════════════════════════════════════════════════

_log_cfg = _app_config.logging
_log_dir = Path(__file__).parent / _log_cfg.dir
_log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, _log_cfg.level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stderr),
        RotatingFileHandler(
            _log_dir / "bepm.log",
            maxBytes=_log_cfg.max_bytes,
            backupCount=_log_cfg.backup_count,
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("bePm")
logger.info("启动 bePm v0.2.0 (production=%s)", _app_config.production)


# ═══════════════════════════════════════════════════════════════════════
# 中间件
# ═══════════════════════════════════════════════════════════════════════

class CharsetMiddleware(BaseHTTPMiddleware):
    """强制响应 Content-Type 包含 charset=utf-8"""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        content_type = response.headers.get("content-type", "")
        path = request.url.path

        if "application/json" in content_type and "charset" not in content_type:
            response.headers["content-type"] = "application/json; charset=utf-8"
        if "text/html" in content_type and "charset" not in content_type:
            response.headers["content-type"] = "text/html; charset=utf-8"
        if "text/css" in content_type and "charset" not in content_type:
            response.headers["content-type"] = "text/css; charset=utf-8"
        if "application/javascript" in content_type and "charset" not in content_type:
            response.headers["content-type"] = "application/javascript; charset=utf-8"

        # 静态资源禁用缓存（开发阶段）；生产环境跳过
        if not _app_config.production:
            _no_cache = any(path.endswith(ext) for ext in (".js", ".css", ".html"))
            # 根路径 / 匹配 StaticFiles 的 index.html，也需要禁用缓存
            _no_cache = _no_cache or path in ("/", "")
            # HTML content-type 响应也需要禁用（StaticFiles html=True 自动路由）
            _no_cache = _no_cache or "text/html" in content_type
            if _no_cache:
                response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"
                # 移除 ETag 防止 304 响应
                if "etag" in response.headers:
                    del response.headers["etag"]

        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """基于内存的简单速率限制（生产环境建议替换为 Redis 方案）"""

    def __init__(self, app):
        super().__init__(app)
        self._windows: dict[str, list[float]] = {}  # key → [timestamps]

    def _check(self, key: str, limit: int) -> bool:
        now = time.time()
        window = self._windows.setdefault(key, [])
        # 清理过期记录（60s 窗口）
        window[:] = [t for t in window if now - t < 60]
        if len(window) >= limit:
            return False
        window.append(now)
        return True

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        rule = _app_config.rate_limit
        client_ip = request.client.host if request.client else "unknown"

        # 根据路径匹配限制规则
        if path.startswith("/api/projects") and request.method == "POST" and not any(
            seg in path for seg in ("/progress", "/command", "/schedule", "/edges", "/nodes")
        ):
            # 创建项目
            if not self._check(f"proj:{client_ip}", rule.projects_per_minute):
                return JSONResponse(
                    status_code=429,
                    content={"detail": f"Too many project creations. Max {rule.projects_per_minute}/min."},
                )
        elif "/command" in path:
            if not self._check(f"cmd:{client_ip}", rule.commands_per_minute):
                return JSONResponse(
                    status_code=429,
                    content={"detail": f"Too many commands. Max {rule.commands_per_minute}/min."},
                )
        elif "/progress" in path and request.method == "POST":
            if not self._check(f"prog:{client_ip}", rule.progress_per_minute):
                return JSONResponse(
                    status_code=429,
                    content={"detail": f"Too many progress updates. Max {rule.progress_per_minute}/min."},
                )

        return await call_next(request)


# ═══════════════════════════════════════════════════════════════════════
# FastAPI 应用
# ═══════════════════════════════════════════════════════════════════════

from api.projects import router as projects_router
from api.ws import router as ws_router

app = FastAPI(
    title="bePm - Project Management Assistant",
    description="AI 辅助项目排期管理系统",
    version="0.2.0",
)

# 中间件顺序：Charset → CORS → RateLimit
app.add_middleware(CharsetMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_app_config.cors.origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Type"],
)

app.add_middleware(RateLimitMiddleware)

# 注册路由
app.include_router(projects_router)
app.include_router(ws_router)


# ═══════════════════════════════════════════════════════════════════════
# 健康检查（深度）
# ═══════════════════════════════════════════════════════════════════════

@app.get("/api/health")
async def health_check():
    """深度健康检查：LLM 连通性 + 存储可写性 + 基本信息"""
    from config import get_anthropic_config

    checks: dict[str, bool | str] = {}
    healthy = True

    # 1. LLM 检查
    from engine.parser import _get_executor
    executor = _get_executor()
    cfg = get_anthropic_config()
    try:
        checks["llm_configured"] = bool(cfg.get("api_key"))
        checks["llm_model"] = cfg.get("model", "unknown")
        if not checks["llm_configured"]:
            healthy = False
    except Exception as e:
        checks["llm_error"] = str(e)[:100]
        healthy = False

    # 2. 存储可写性检查
    try:
        projects_dir = Path(__file__).parent.parent / ".projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        test_file = projects_dir / ".health_check"
        test_file.write_text("ok")
        test_file.unlink()
        checks["storage_writable"] = True
    except Exception as e:
        checks["storage_error"] = str(e)[:100]
        healthy = False

    # 3. 磁盘空间检查（警告 < 100MB）
    try:
        import shutil
        usage = shutil.disk_usage(projects_dir)
        checks["disk_free_mb"] = usage.free // (1024 * 1024)
        if usage.free < 100 * 1024 * 1024:
            checks["disk_warning"] = "low"
    except Exception:
        pass

    return {
        "status": "ok" if healthy else "degraded",
        "service": "bePm",
        "version": "0.2.0",
        "production": _app_config.production,
        "encoding": sys.getdefaultencoding(),
        "llm_provider": executor.provider,
        "llm_sdk_type": executor.sdk_type,
        "llm_model": cfg.get("model", "unknown"),
        "checks": checks,
    }


# ═══════════════════════════════════════════════════════════════════════
# 异常处理（生产模式隐藏详细信息）
# ═══════════════════════════════════════════════════════════════════════

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc):
    if exc.status_code == 404:
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=404,
                content={"detail": f"API endpoint not found: {request.url.path}"},
            )
        return Response(
            content=f"Not Found: {request.url.path}",
            status_code=404,
            media_type="text/plain",
        )

    # 非 404 的 HTTPException
    detail = exc.detail
    if _app_config.production and exc.status_code >= 500:
        detail = "Internal server error"
        logger.error("HTTP %d at %s: %s", exc.status_code, request.url.path, exc.detail)

    return JSONResponse(status_code=exc.status_code, content={"detail": detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """全局未捕获异常处理"""
    logger.error(
        "Unhandled error at %s %s: %s",
        request.method, request.url.path, exc,
        exc_info=True,
    )
    if _app_config.production:
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {exc}"},
    )


# ═══════════════════════════════════════════════════════════════════════
# 静态文件
# ═══════════════════════════════════════════════════════════════════════

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


# ═══════════════════════════════════════════════════════════════════════
# 优雅关闭
# ═══════════════════════════════════════════════════════════════════════

_shutting_down = False


@app.get("/api/health/ready")
async def readiness_check():
    """K8s readiness probe"""
    return {"status": "not_ready" if _shutting_down else "ready"}


def _graceful_shutdown(signum, frame):
    global _shutting_down
    logger.info("收到信号 %s，开始优雅关闭...", signum.name if hasattr(signum, "name") else signum)
    _shutting_down = True


signal.signal(signal.SIGTERM, _graceful_shutdown)
signal.signal(signal.SIGINT, _graceful_shutdown)


# ═══════════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", str(_app_config.server.host))
    port = int(os.environ.get("PORT", str(_app_config.server.port)))

    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["formatters"]["default"]["fmt"] = "%(asctime)s [%(levelname)s] %(message)s"
    log_config["formatters"]["default"]["datefmt"] = "%Y-%m-%dT%H:%M:%S"

    logger.info("监听 http://%s:%s", host, port)
    uvicorn.run(app, host=host, port=port, log_config=log_config)
