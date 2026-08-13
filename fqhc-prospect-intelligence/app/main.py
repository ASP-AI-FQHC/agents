"""FastAPI application entry point.

Run with::

    uvicorn app.main:app --reload

At this stage the app exposes the brand shell and a build-status page; the
dashboard, detail view, review queue and exports are added as their pipeline
modules land.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import inspect

from app.config import get_config
from app.db import get_engine, init_db

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# Brand strings (company name, app name) are read from config in every template.
templates.env.globals["config"] = get_config()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Creating tables at startup keeps the app runnable before the pipeline has
    # ever been executed -- the UI then simply reports an empty database.
    init_db()
    yield


config = get_config()
app = FastAPI(title=config.app.name, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok", "app": config.app.name})


@app.get("/")
def index(request: Request):
    """Build-status page: what the scaffold created and what is wired up."""
    inspector = inspect(get_engine())
    tables = sorted(inspector.get_table_names())
    return templates.TemplateResponse(
        request,
        "scaffold.html",
        {
            "tables": tables,
            "database_file": config.database_file,
            "cache_directory": config.cache_directory,
        },
    )
