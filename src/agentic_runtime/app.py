from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .dashboard import get_dashboard_data, get_project_detail_data, get_project_graph_data, get_task_detail_data
from .db import connect, initialize_database


PACKAGE_ROOT = Path(__file__).resolve().parent


def create_app(db_path: str = "runtime/runtime.db") -> FastAPI:
    initialize_database(db_path).close()

    app = FastAPI(title="openDAGent", version="0.1.0")
    app.state.db_path = db_path
    templates = Jinja2Templates(directory=str(PACKAGE_ROOT / "templates"))

    app.mount(
        "/static",
        StaticFiles(directory=str(PACKAGE_ROOT / "static")),
        name="static",
    )

    def open_connection() -> sqlite3.Connection:
        return connect(app.state.db_path)

    @app.get("/healthz")
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        connection = open_connection()
        try:
            context = get_dashboard_data(connection)
        finally:
            connection.close()
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context=context,
        )

    @app.get("/projects/{project_id}", response_class=HTMLResponse)
    async def project_detail(request: Request, project_id: str) -> HTMLResponse:
        connection = open_connection()
        try:
            context = get_project_detail_data(connection, project_id)
        finally:
            connection.close()
        if context is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return templates.TemplateResponse(
            request=request,
            name="project_detail.html",
            context=context,
        )

    @app.get("/tasks/{task_id}", response_class=HTMLResponse)
    async def task_detail(request: Request, task_id: str) -> HTMLResponse:
        connection = open_connection()
        try:
            context = get_task_detail_data(connection, task_id)
        finally:
            connection.close()
        if context is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return templates.TemplateResponse(
            request=request,
            name="task_detail.html",
            context=context,
        )

    @app.get("/api/projects/{project_id}/graph")
    async def project_graph(project_id: str) -> JSONResponse:
        connection = open_connection()
        try:
            graph = get_project_graph_data(connection, project_id)
        finally:
            connection.close()
        if graph is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return JSONResponse(graph)

    return app
