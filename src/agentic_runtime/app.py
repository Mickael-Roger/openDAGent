from __future__ import annotations

import sqlite3
from importlib import import_module
from pathlib import Path
from typing import Any

from .capabilities import register_builtins
from .dashboard import (
    add_user_message,
    create_project_with_goal,
    get_dashboard_data,
    get_project_chat_data,
    get_project_detail_data,
    get_project_graph_data,
    get_task_detail_data,
)
from .db import connect, initialize_database

try:
    from fastapi import Request as _FastAPIRequest
except ImportError:  # pragma: no cover - fastapi may be absent in minimal environments
    _FastAPIRequest = None  # type: ignore[assignment,misc]


PACKAGE_ROOT = Path(__file__).resolve().parent


def create_app(db_path: str = "runtime/runtime.db") -> Any:
    fastapi_module = import_module("fastapi")
    responses_module = import_module("fastapi.responses")
    staticfiles_module = import_module("fastapi.staticfiles")
    templating_module = import_module("fastapi.templating")

    FastAPI = fastapi_module.FastAPI
    HTTPException = fastapi_module.HTTPException
    HTMLResponse = responses_module.HTMLResponse
    JSONResponse = responses_module.JSONResponse
    StaticFiles = staticfiles_module.StaticFiles
    Jinja2Templates = templating_module.Jinja2Templates

    conn = initialize_database(db_path)
    register_builtins(conn)
    conn.close()

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
    async def dashboard(request: _FastAPIRequest) -> Any:
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
    async def project_detail(request: _FastAPIRequest, project_id: str) -> Any:
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
    async def task_detail(request: _FastAPIRequest, task_id: str) -> Any:
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
    async def project_graph(project_id: str) -> Any:
        connection = open_connection()
        try:
            graph = get_project_graph_data(connection, project_id)
        finally:
            connection.close()
        if graph is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return JSONResponse(graph)

    @app.get("/chat/new", response_class=HTMLResponse)
    async def chat_new(request: _FastAPIRequest) -> Any:
        return templates.TemplateResponse(
            request=request,
            name="chat_new.html",
            context={"page_title": "New Project"},
        )

    @app.post("/api/chat")
    async def api_create_chat(request: _FastAPIRequest) -> Any:
        body = await request.json()
        title = str(body.get("title", "")).strip()
        description = str(body.get("description", "")).strip()
        if not title:
            raise HTTPException(status_code=400, detail="title is required")
        connection = open_connection()
        try:
            project_id, _goal_id = create_project_with_goal(connection, title, description)
        finally:
            connection.close()
        return JSONResponse({"project_id": project_id})

    @app.get("/projects/{project_id}/chat", response_class=HTMLResponse)
    async def project_chat(request: _FastAPIRequest, project_id: str) -> Any:
        connection = open_connection()
        try:
            context = get_project_chat_data(connection, project_id)
        finally:
            connection.close()
        if context is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return templates.TemplateResponse(
            request=request,
            name="chat.html",
            context=context,
        )

    @app.post("/api/projects/{project_id}/messages")
    async def api_add_message(request: _FastAPIRequest, project_id: str) -> Any:
        body = await request.json()
        content = str(body.get("content", "")).strip()
        if not content:
            raise HTTPException(status_code=400, detail="content is required")
        connection = open_connection()
        try:
            context = get_project_chat_data(connection, project_id)
            if context is None:
                raise HTTPException(status_code=404, detail="Project not found")
            if context["goal"] is None:
                raise HTTPException(status_code=400, detail="Project has no goal")
            message = add_user_message(
                connection, project_id, context["goal"]["goal_id"], content
            )
        finally:
            connection.close()
        return JSONResponse(message)

    @app.get("/api/projects/{project_id}/messages")
    async def api_get_messages(project_id: str, after: str | None = None) -> Any:
        connection = open_connection()
        try:
            context = get_project_chat_data(connection, project_id)
            if context is None:
                raise HTTPException(status_code=404, detail="Project not found")
            messages = context["messages"]
            if after:
                messages = [m for m in messages if m["message_ts"] > after]
        finally:
            connection.close()
        return JSONResponse({"messages": messages})

    return app
