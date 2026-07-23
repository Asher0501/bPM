# -*- coding: utf-8 -*-
"""bePm — AI 辅助项目排期管理系统 · 后端入口"""

import os
import sys
import io
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response, JSONResponse

from config import init_env

# ═══════════════════════════════════════════════════════════════════════
# Windows 编码修复：强制 stdout/stderr 使用 UTF-8
# 解决 uvicorn 日志在中文 Windows 下的乱码问题（参考 Nexus 的 env_filter 模式）
# ═══════════════════════════════════════════════════════════════════════

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )
    # 设置控制台代码页为 UTF-8（对子进程生效）
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 初始化环境变量（从 ~/.claude/settings.json 注入）
init_env()


# ═══════════════════════════════════════════════════════════════════════
# 响应编码中间件：确保所有 JSON 响应声明 charset=utf-8
# ═══════════════════════════════════════════════════════════════════════

class CharsetMiddleware(BaseHTTPMiddleware):
    """强制所有响应的 Content-Type 包含 charset=utf-8，并对静态资源禁用缓存。

    虽然 RFC 8259 规定 JSON 默认为 UTF-8，但显式声明 charset 可以避免
    某些客户端（如 Windows cmd.exe curl）的编码检测问题。
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        content_type = response.headers.get("content-type", "")
        path = request.url.path

        # charset 修正
        if "application/json" in content_type and "charset" not in content_type:
            response.headers["content-type"] = "application/json; charset=utf-8"
        if "text/html" in content_type and "charset" not in content_type:
            response.headers["content-type"] = "text/html; charset=utf-8"
        if "text/css" in content_type and "charset" not in content_type:
            response.headers["content-type"] = "text/css; charset=utf-8"
        if "application/javascript" in content_type and "charset" not in content_type:
            response.headers["content-type"] = "application/javascript; charset=utf-8"

        # 静态资源禁用缓存（开发阶段）
        if any(path.endswith(ext) for ext in (".js", ".css", ".html")):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"

        return response


from api.projects import router as projects_router
from api.ws import router as ws_router

app = FastAPI(
    title="bePm - Project Management Assistant",
    description="AI 辅助项目排期管理系统",
    version="0.1.0",
)

# 响应编码中间件（必须在 CORS 之前）
app.add_middleware(CharsetMiddleware)

# CORS — 允许前端开发时的跨域请求
# 注意：allow_origins=["*"] 与 allow_credentials=True 不兼容（浏览器会拒绝带 cookie 的响应）
# 开发阶段显式列出允许的来源
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:48090",
        "http://localhost:48090",
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "http://127.0.0.1:8080",
        "http://localhost:8080",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Type"],
)

# 注册路由
app.include_router(projects_router)
app.include_router(ws_router)


@app.get("/api/health")
async def health_check():
    from engine.parser import _get_executor
    from config import get_anthropic_config
    executor = _get_executor()
    cfg = get_anthropic_config()
    return {
        "status": "ok",
        "service": "bePm",
        "encoding": sys.getdefaultencoding(),
        "llm_provider": executor.provider,
        "llm_sdk_type": executor.sdk_type,
        "llm_model": cfg.get("model", "unknown"),
    }


# ---- API 404 处理（未匹配的 /api/* 路径返回 JSON 而非 HTML） ----
@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=404,
            content={"detail": f"API endpoint not found: {request.url.path}"},
        )
    # 非 API 路径交给 StaticFiles 处理
    return None


# 静态文件服务：serve frontend/ 目录（必须在所有 API 路由之后）
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "48090"))
    uvicorn.run(app, host=host, port=port)
