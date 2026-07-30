from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from safefix.web.routes import create_router


@dataclass(frozen=True, slots=True)
class AppDependencies:
    service: Any
    public_demo: bool = False
    embedded_project: str = "examples/python_bug"
    public_rate_limit: int = 30
    public_active_run_limit: int = 1


def create_app(dependencies: AppDependencies) -> FastAPI:
    app = FastAPI(title="SafeFix", version="0.1.1")
    static_dir = Path(__file__).with_name("static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.exception_handler(HTTPException)
    async def stable_http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": exc.detail}
        return JSONResponse(status_code=exc.status_code, content={"error": detail})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(create_router(dependencies))
    return app
