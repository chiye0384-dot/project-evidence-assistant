"""Local registry, public-source tools and bounded model-independent tasks."""

from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import httpx
import sqlite3

from .models import (ExcerptRequest, FindRequest, ProjectRecord, ProjectRegistration,
                     SearchRequest, VersionSelection)
from .public_verify import PublicGitHubVerifier
from .security import LocalRequestMiddleware
from .source import SourceError
from .source_service import SourceService
from .storage import ProjectStore
from .structure import summarize
from .task_service import TaskService
from .learning_contract import OutlineSelection, SectionSelection
from .reading_progress import ReadingProgress, ReadingMark
from .learning_contract import FollowUpSelection
from pydantic import BaseModel, ConfigDict


class DirectoryTaskRequest(BaseModel):
    # No HTTP hooks for adapters, secrets, limits, arbitrary goals or new tools.
    model_config = ConfigDict(extra="forbid")


def create_app(data_dir: Path | None = None, *, github_transport: httpx.BaseTransport | None = None,
               git_executable: str | None = None, model_adapter=None) -> FastAPI:
    project_root = Path(__file__).resolve().parents[2]
    static_dir = project_root / "static"
    store = ProjectStore(data_dir if data_dir is not None else project_root / "data")
    sources = SourceService(store, PublicGitHubVerifier(github_transport), git_executable)
    tasks = TaskService(store, sources, model_adapter)
    reading = ReadingProgress(store)

    @asynccontextmanager
    async def lifespan(app):
        yield
        tasks.shutdown()

    app = FastAPI(title="项目学习证据助手", version="0.4.0", lifespan=lifespan)
    app.state.project_store = store
    app.state.source_service = sources
    app.state.task_service = tasks
    app.state.reading_progress = reading
    app.add_middleware(LocalRequestMiddleware)

    @app.exception_handler(SourceError)
    async def source_error(request: Request, error: SourceError) -> JSONResponse:
        return JSONResponse({"status": error.code, "message": error.message}, status_code=200)

    def project(project_id: UUID) -> ProjectRecord:
        record = store.get(project_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return record

    def display(record: ProjectRecord) -> ProjectRecord:
        return record.model_copy(update={"source_status": "verified" if sources.active(record) else "unverified"})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        if request.url.path.endswith("/learning/progress"):
            return JSONResponse({"status": "invalid_reading_mark", "message": "阅读标记格式不符合要求；没有保存或发起模型请求。"}, status_code=422)
        if request.url.path.endswith("/learning/followup"):
            return JSONResponse({"status": "invalid_followup", "message": "追问格式不符合要求；请只填写当前小节的问题，不要包含密钥、本机路径或受保护内容。未保存或调用模型。"}, status_code=422)
        # Never echo arbitrary rejected input (which could contain credentials)
        # into errors or logs. Keep field locations and safe validation messages.
        details = [
            {"loc": item["loc"], "msg": item["msg"], "type": item["type"]}
            for item in error.errors()
        ]
        return JSONResponse({"detail": details}, status_code=422)

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "model_status": "not_configured" if not tasks.adapter.is_configured else "scripted_test" if tasks.adapter.execution_mode == "scripted" else "configured",
            "stage": "M1-engine",
            "capabilities": {
                "project_registry": True,
                "source_analysis": False,
                "agent_generation": tasks.adapter.is_configured and tasks.adapter.execution_mode == "real",
                "agent_tasks": True,
                "source_tools": True,
                "structure_facts": True,
                "learning_records": True,
                "query_generation": tasks.query_enabled() and tasks.adapter.execution_mode == "real",
                "submission_generation": tasks.topic_enabled("submission") and tasks.adapter.execution_mode == "real",
                "consumption_generation": tasks.topic_enabled("consumption") and tasks.adapter.execution_mode == "real",
                "judging_generation": tasks.topic_enabled("judging") and tasks.adapter.execution_mode == "real",
            },
        }

    @app.post("/api/projects", response_model=ProjectRecord, status_code=201)
    def register_project(registration: ProjectRegistration) -> ProjectRecord:
        return store.create(registration)

    @app.get("/api/model/status")
    def model_status() -> dict:
        if hasattr(tasks.adapter, "public_status"):
            result = {"model_status": "configured", "provider": "deepseek", "model": "deepseek-flash",
                    "followup_protocol": getattr(tasks.adapter, "followup_protocol", "responses") if getattr(tasks.adapter, "enable_followup", False) else "disabled",
                    "spending": tasks.adapter.public_status()}
            if getattr(tasks.adapter, 'followup_claim_format', 'literal') in ('selected_parts_v1', 'selected_table_v1'):
                result['followup_claim_format'] = tasks.adapter.followup_claim_format
            if getattr(tasks.adapter, 'followup_prose_format', 'indexed') == 'kind_prose_v1':
                result['followup_prose_format'] = 'kind_prose_v1'
            if getattr(tasks.adapter, 'followup_scope_view', 'off') in ('java_scope_edge_view_v1', 'java_scope_edge_unit_columns_v1', 'java_scope_edge_unit_index_columns_v1'):
                result['followup_scope_view'] = tasks.adapter.followup_scope_view
            route = getattr(tasks.adapter, 'network_route', None)
            if route is not None:
                result['network_route'] = route
            return result
        return {"model_status": "not_configured" if not tasks.adapter.is_configured else "scripted_test",
                "spending": {"status": "not_enabled", "currency": "CNY"}}

    @app.get("/api/projects", response_model=list[ProjectRecord])
    def list_projects() -> list[ProjectRecord]:
        return [display(record) for record in store.list()]

    @app.get("/api/projects/{project_id}", response_model=ProjectRecord)
    def get_project(project_id: UUID) -> ProjectRecord:
        return display(project(project_id))

    @app.delete("/api/projects/{project_id}", status_code=204, response_class=Response)
    def delete_project(project_id: UUID) -> Response:
        with tasks.project_change(project_id):
            if not store.delete(project_id):
                raise HTTPException(status_code=404, detail="Project not found")
            sources.clear(project_id)
        return Response(status_code=204)

    @app.patch("/api/projects/{project_id}/version", response_model=ProjectRecord)
    def select_version(project_id: UUID, selection: VersionSelection) -> ProjectRecord:
        with tasks.project_change(project_id):
            project(project_id)
            sources.clear(project_id)
            updated = store.set_version(project_id, selection.commit)
        if updated is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return updated

    @app.post("/api/projects/{project_id}/source/verify")
    def verify_source(project_id: UUID) -> dict:
        return sources.verify(project(project_id))

    @app.get("/api/projects/{project_id}/source/status")
    def source_status(project_id: UUID) -> dict:
        return sources.status(project(project_id))

    @app.get("/api/projects/{project_id}/structure")
    def structure(project_id: UUID) -> dict:
        record = project(project_id)
        # Require a live public-version session even if an earlier scan is cached.
        tools = sources.tools(record)
        cached = store.structure(project_id, record.commit)
        if cached is not None:
            return cached
        result = summarize(tools)
        if result.get("status") == "ok":
            try:
                store.save_structure(project_id, record.commit, result)
            except sqlite3.IntegrityError:
                raise HTTPException(status_code=404, detail="Project not found") from None
        return result

    @app.post("/api/projects/{project_id}/tools/find")
    def find_files(project_id: UUID, parameters: FindRequest) -> dict:
        return sources.tools(project(project_id)).find_files(**parameters.model_dump())

    @app.post("/api/projects/{project_id}/tools/search")
    def search_text(project_id: UUID, parameters: SearchRequest) -> dict:
        return sources.tools(project(project_id)).search_text(**parameters.model_dump())

    @app.post("/api/projects/{project_id}/tools/read")
    def read_excerpt(project_id: UUID, parameters: ExcerptRequest) -> dict:
        record = project(project_id)
        result = sources.tools(record).read_excerpt(**parameters.model_dump())
        if result.get("status") == "ok" and result.get("text"):
            try:
                saved = store.save_evidence(project_id, result)
            except sqlite3.IntegrityError:
                raise HTTPException(status_code=404, detail="Project not found") from None
            except ValueError:
                raise SourceError("limit_exceeded", "本项目证据卡达到1000条上限；原有记录仍保留。") from None
            result["evidence_id"] = saved["id"]
        return result

    @app.get("/api/projects/{project_id}/evidence")
    def evidence(project_id: UUID, limit: int = Query(50, ge=1, le=100),
                 offset: int = Query(0, ge=0, le=1000)) -> list[dict]:
        return sources.evidence(project(project_id), limit, offset)

    @app.get("/api/projects/{project_id}/evidence/{evidence_id}")
    def saved_evidence(project_id: UUID, evidence_id: UUID) -> dict:
        item = sources.evidence_by_id(project(project_id), evidence_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Evidence not found")
        return item

    @app.get("/api/projects/{project_id}/tasks")
    def list_tasks(project_id: UUID, limit: int = Query(100, ge=1, le=100),
                   offset: int = Query(0, ge=0, le=999)) -> list[dict]:
        return tasks.list(project(project_id), limit=limit, offset=offset)

    @app.get("/api/projects/{project_id}/tasks/capacity")
    def task_capacity(project_id: UUID) -> dict:
        record = project(project_id)
        return tasks.history.capacity(record.id)

    @app.post("/api/projects/{project_id}/tasks")
    def start_task(project_id: UUID, parameters: DirectoryTaskRequest) -> dict:
        return tasks.start(project(project_id))

    @app.get("/api/projects/{project_id}/tasks/{task_id}")
    def get_task(project_id: UUID, task_id: UUID) -> dict:
        task = tasks.get(project(project_id), task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return task

    @app.get("/api/projects/{project_id}/learning/status")
    def learning_status(project_id: UUID) -> dict:
        project(project_id)
        return {"query_enabled": tasks.query_enabled(), "submission_enabled": tasks.topic_enabled("submission"),
                "consumption_enabled": tasks.topic_enabled("consumption"),
                "judging_enabled": tasks.topic_enabled("judging"),
                "followup_enabled": tasks.followup_enabled(),
                "execution_mode": tasks.adapter.execution_mode}

    @app.get("/api/projects/{project_id}/learning/progress")
    def reading_progress(project_id: UUID) -> list[dict]:
        return reading.list(project(project_id))

    @app.put("/api/projects/{project_id}/learning/progress")
    def mark_reading(project_id: UUID, parameters: ReadingMark) -> dict:
        try:
            return reading.mark(project(project_id), parameters)
        except ValueError:
            raise HTTPException(status_code=422, detail="所选内容或阅读标记不可用；需复核的正文不能新增本人已懂标记。") from None

    @app.get("/api/projects/{project_id}/learning")
    def learning_content(project_id: UUID) -> list[dict]:
        return tasks.learning.list(project(project_id))

    @app.get("/api/projects/{project_id}/learning/{content_id}")
    def learning_record(project_id: UUID, content_id: UUID) -> dict:
        item = tasks.learning.get(project(project_id), content_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Learning record not found")
        return item

    @app.post("/api/projects/{project_id}/learning/outline")
    def start_outline(project_id: UUID, selection: OutlineSelection) -> dict:
        return tasks.start(project(project_id), kind="chapter_outline", inputs=selection.model_dump())

    @app.post("/api/projects/{project_id}/learning/section")
    def start_section(project_id: UUID, selection: SectionSelection) -> dict:
        return tasks.start(project(project_id), kind="section_explanation", inputs=selection.model_dump())

    @app.post("/api/projects/{project_id}/learning/followup")
    def start_followup(project_id: UUID, selection: FollowUpSelection) -> dict:
        return tasks.start(project(project_id), kind="follow_up", inputs=selection.model_dump())

    @app.post("/api/projects/{project_id}/tasks/{task_id}/cancel")
    def cancel_task(project_id: UUID, task_id: UUID) -> dict:
        task = tasks.cancel(project(project_id), task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return task

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        if not (static_dir / "index.html").is_file():
            raise HTTPException(status_code=503, detail="Local interface is not available yet")
        return FileResponse(static_dir / "index.html")

    app.mount("/static", StaticFiles(directory=static_dir, check_dir=False), name="static")
    return app
