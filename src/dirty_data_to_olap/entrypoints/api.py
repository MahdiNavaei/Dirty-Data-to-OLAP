"""Versioned FastAPI transport for the Step27 backend boundary.

The router only translates HTTP into application commands.  It does not call
SQLite, artifact paths, providers, SQL, or data-processing engines directly.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
import re
from typing import Any

from fastapi import FastAPI, Header, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from dirty_data_to_olap.application.backend import BackendError, BackendService, Principal
from dirty_data_to_olap.domain.contracts.canonical import (
    ReviewCheckpoint,
    ReviewCompatibilityContext,
)
from dirty_data_to_olap.domain.contracts.jobs import JobRecord
from dirty_data_to_olap.domain.contracts.platform import ArtifactRef, RunRecord, StageAttemptRecord


_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_FILTER = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ErrorDetail(ApiModel):
    code: str
    message: str
    status: int
    retryable: bool = False
    request_id: str


class ErrorEnvelope(ApiModel):
    error: ErrorDetail


class CreateRunRequest(ApiModel):
    project_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    configuration_fingerprint: str = Field(min_length=1, max_length=256)
    git_content_commit: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{40}$")
    metadata: dict[str, str] = Field(default_factory=dict, max_length=32)

    @field_validator("metadata")
    @classmethod
    def metadata_is_safe(cls, value: dict[str, str]) -> dict[str, str]:
        secret = re.compile(r"(?i)(?:password|passwd|secret|token|api[_-]?key|credential)")
        if any(secret.search(str(k)) or secret.search(str(v)) for k, v in value.items()):
            raise ValueError("metadata contains a restricted field")
        return value


class RegisterArtifactRequest(ApiModel):
    artifact_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class ReviewActionDecision(str, Enum):
    """Step27 actions; SKIPPED is not exposed without server authorization."""

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"


class ReviewActionRequest(ApiModel):
    subject_artifact_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    subject_content_hash: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[a-fA-F0-9]{64}$")
    context: ReviewCompatibilityContext | None = None
    decision: ReviewActionDecision
    rationale: str = Field(min_length=1, max_length=2000)
    expected_revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def subject_identity_is_complete(self) -> "ReviewActionRequest":
        if self.context is None and (self.subject_artifact_id is None or self.subject_content_hash is None):
            raise ValueError("subject_artifact_id and subject_content_hash are required when context is omitted")
        if (self.subject_artifact_id is None) != (self.subject_content_hash is None):
            raise ValueError("subject artifact identity and content hash must be supplied together")
        if self.context is not None and self.subject_artifact_id is not None and self.context.subject_artifact_id != self.subject_artifact_id:
            raise ValueError("subject_artifact_id must match the context assertion")
        if self.context is not None and self.subject_content_hash is not None and self.context.subject_content_hash != self.subject_content_hash:
            raise ValueError("subject_content_hash must match the context assertion")
        return self

    @field_validator("rationale")
    @classmethod
    def rationale_is_safe(cls, value: str) -> str:
        if re.search(r"(?i)(?:password|passwd|secret|token|api[_-]?key|credential)\s*[:=]", value):
            raise ValueError("rationale contains a restricted secret-shaped field")
        return value


class PageResponse(ApiModel):
    items: list[Any]
    page_size: int
    offset: int
    next_offset: int | None
    order_by: str


class RunView(ApiModel):
    """Safe run projection; internal metadata is intentionally omitted."""

    schema_version: str
    run_id: str
    project_id: str
    created_at: str
    status: str
    configuration_fingerprint: str
    git_content_commit: str | None
    root_artifact_refs: tuple[str, ...]
    source_snapshot_refs: tuple[str, ...]
    gate_refs: tuple[str, ...]
    latest_stage_refs: dict[str, str]
    revision: int


class ArtifactView(ApiModel):
    artifact_id: str
    run_id: str
    stage_id: str
    attempt_id: str
    artifact_kind: str
    schema_version: str
    media_type: str
    content_hash: str
    byte_size: int
    storage_mode: str
    logical_key: str | None
    producer: str
    retention_class: str
    restricted: bool
    publication_state: str
    provenance_refs: tuple[str, ...]


class InvalidateReviewRequest(ApiModel):
    subject_artifact_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    subject_content_hash: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[a-fA-F0-9]{64}$")
    context: ReviewCompatibilityContext | None = None
    reason: str = Field(min_length=1, max_length=1000)
    expected_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def subject_identity_is_complete(self) -> "InvalidateReviewRequest":
        if self.context is None and (self.subject_artifact_id is None or self.subject_content_hash is None):
            raise ValueError("subject_artifact_id and subject_content_hash are required when context is omitted")
        if (self.subject_artifact_id is None) != (self.subject_content_hash is None):
            raise ValueError("subject artifact identity and content hash must be supplied together")
        if self.context is not None and self.subject_artifact_id is not None and self.context.subject_artifact_id != self.subject_artifact_id:
            raise ValueError("subject_artifact_id must match the context assertion")
        if self.context is not None and self.subject_content_hash is not None and self.context.subject_content_hash != self.subject_content_hash:
            raise ValueError("subject_content_hash must match the context assertion")
        return self


def _artifact_view(artifact: ArtifactRef) -> ArtifactView:
    """Project an artifact ref without host paths or external locators."""

    return ArtifactView(
        artifact_id=artifact.artifact_id,
        run_id=artifact.run_id,
        stage_id=artifact.stage_id,
        attempt_id=artifact.attempt_id,
        artifact_kind=artifact.artifact_kind,
        schema_version=artifact.schema_version,
        media_type=artifact.media_type,
        content_hash=artifact.content_hash,
        byte_size=artifact.byte_size,
        storage_mode=artifact.storage_mode.value,
        logical_key=artifact.logical_key,
        producer=artifact.producer,
        retention_class=artifact.retention_class.value,
        restricted=artifact.sensitivity_ref is not None,
        publication_state=artifact.publication_state.value,
        provenance_refs=tuple(sorted(artifact.provenance_refs)),
    )


def _run_view(run: RunRecord) -> RunView:
    return RunView.model_validate(run.model_dump(mode="json", exclude={"metadata"}))


def _model_page(page: Any, mapper: Callable[[Any], Any] | None = None) -> dict[str, Any]:
    convert = mapper or (lambda value: value)
    return {
        "items": [
            converted.model_dump(mode="json") if hasattr((converted := convert(item)), "model_dump") else converted
            for item in page.items
        ],
        "page_size": page.page_size,
        "offset": page.offset,
        "next_offset": page.next_offset,
        "order_by": page.order_by,
    }


def _safe_request_id(request: Request) -> str:
    value = request.headers.get("X-Request-ID", "local-request")
    return value[:128] if re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value) else "local-request"


def create_app(
    backend: BackendService,
    *,
    auth_mode: str = "local_test",
    principal_resolver: Callable[[Request], Principal | None] | None = None,
) -> FastAPI:
    """Create the executable, explicitly versioned local reference API.

    ``local_test`` is intentionally explicit and is not production
    authentication.  A deployment may inject a trusted resolver later; this
    module does not implement passwords, JWTs, or a credential store.
    """

    if auth_mode not in {"local_test", "trusted_proxy"}:
        raise ValueError("unsupported auth mode")
    app = FastAPI(
        title="Dirty Data to OLAP Backend API",
        version="0.1.0-step27",
        description="Bounded V1 control-plane API. Heavy execution is delegated to a separate port.",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
    )

    def principal(request: Request) -> Principal:
        if auth_mode == "trusted_proxy":
            resolved = principal_resolver(request) if principal_resolver is not None else None
            if resolved is None:
                raise BackendError("UNAUTHENTICATED", "a trusted principal is required", status=401)
            return resolved
        subject = request.headers.get("X-Local-Principal")
        if subject is None or not _TOKEN.fullmatch(subject):
            raise BackendError("UNAUTHENTICATED", "local test authentication requires X-Local-Principal", status=401)
        return Principal(
            subject=subject,
            scopes=frozenset({"runs:read", "runs:write", "attempts:read", "jobs:read", "reviews:read", "reviews:write", "artifacts:read", "artifacts:write", "validation:read", "visualizations:read"}),
            source="LOCAL_TEST_AUTH",
        )

    def read_principal(request: Request, scope: str) -> Principal:
        caller = principal(request)
        backend.authorize_read(caller, scope)
        return caller

    def key(value: str | None) -> str:
        if value is None:
            raise BackendError("IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key is required for this mutation", status=400)
        return value

    @app.exception_handler(BackendError)
    async def backend_error_handler(request: Request, exc: BackendError) -> JSONResponse:
        body = ErrorEnvelope(error=ErrorDetail(code=exc.code, message=exc.message, status=exc.status, retryable=exc.retryable, request_id=_safe_request_id(request)))
        return JSONResponse(status_code=exc.status, content=body.model_dump(mode="json"))

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = sorted({".".join(str(part) for part in error.get("loc", ())) for error in exc.errors()})
        message = "request contract validation failed" if not fields else "request contract validation failed for: " + ", ".join(fields[:20])
        body = ErrorEnvelope(error=ErrorDetail(code="INVALID_REQUEST", message=message, status=422, request_id=_safe_request_id(request)))
        return JSONResponse(status_code=422, content=body.model_dump(mode="json"))

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        message = "resource was not found" if exc.status_code == 404 else "HTTP request was rejected"
        body = ErrorEnvelope(error=ErrorDetail(code="HTTP_ERROR", message=message, status=exc.status_code, request_id=_safe_request_id(request)))
        return JSONResponse(status_code=exc.status_code, content=body.model_dump(mode="json"))

    @app.exception_handler(Exception)
    async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
        body = ErrorEnvelope(error=ErrorDetail(code="INTERNAL_ERROR", message="an unexpected backend error occurred", status=500, request_id=_safe_request_id(request)))
        return JSONResponse(status_code=500, content=body.model_dump(mode="json"))

    @app.get("/api/v1/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "api_version": "v1"}

    @app.post("/api/v1/runs", status_code=201, response_model=RunView, tags=["runs"])
    async def create_run(payload: CreateRunRequest, request: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> JSONResponse:
        run, replayed = backend.create_run(
            project_id=payload.project_id,
            configuration_fingerprint=payload.configuration_fingerprint,
            git_content_commit=payload.git_content_commit,
            metadata=payload.metadata,
            principal=principal(request),
            idempotency_key=key(idempotency_key),
        )
        response = JSONResponse(status_code=201, content=_run_view(run).model_dump(mode="json"))
        response.headers["Idempotency-Replayed"] = "true" if replayed else "false"
        response.headers["ETag"] = f'"{run.revision}"'
        return response

    @app.get("/api/v1/runs", response_model=PageResponse, tags=["runs"])
    async def list_runs(
        request: Request,
        project_id: str | None = Query(default=None, max_length=128),
        status: str | None = Query(default=None, max_length=32),
        page_size: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        read_principal(request, "runs:read")
        return _model_page(backend.list_runs(project_id=project_id, status=status, page_size=page_size, offset=offset), _run_view)

    @app.get("/api/v1/projects/{project_id}/runs", response_model=PageResponse, tags=["projects"])
    async def list_project_runs(request: Request, project_id: str = Path(min_length=1, max_length=128), status: str | None = Query(default=None, max_length=32), page_size: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)) -> dict[str, Any]:
        read_principal(request, "runs:read")
        return _model_page(backend.list_runs(project_id=project_id, status=status, page_size=page_size, offset=offset), _run_view)

    @app.get("/api/v1/runs/{run_id}", response_model=RunView, tags=["runs"])
    async def get_run(request: Request, run_id: str = Path(min_length=1, max_length=128)) -> RunView:
        read_principal(request, "runs:read")
        return _run_view(backend.get_run(run_id))

    @app.get("/api/v1/runs/{run_id}/attempts", response_model=PageResponse, tags=["attempts"])
    async def list_attempts(
        request: Request,
        run_id: str = Path(min_length=1, max_length=128),
        stage_id: str | None = Query(default=None, max_length=128),
        status: str | None = Query(default=None, max_length=32),
        page_size: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        read_principal(request, "attempts:read")
        return _model_page(backend.list_attempts(run_id=run_id, stage_id=stage_id, status=status, page_size=page_size, offset=offset))

    @app.get("/api/v1/runs/{run_id}/attempts/{attempt_id}", response_model=StageAttemptRecord, tags=["attempts"])
    async def get_attempt(request: Request, run_id: str = Path(min_length=1, max_length=128), attempt_id: str = Path(min_length=1, max_length=128)) -> StageAttemptRecord:
        read_principal(request, "attempts:read")
        return backend.get_attempt(run_id=run_id, attempt_id=attempt_id)

    @app.get("/api/v1/runs/{run_id}/jobs", response_model=PageResponse, tags=["jobs"])
    async def list_jobs(
        request: Request,
        run_id: str = Path(min_length=1, max_length=128),
        status: str | None = Query(default=None, max_length=32),
        page_size: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        read_principal(request, "jobs:read")
        return _model_page(backend.list_jobs(run_id=run_id, status=status, page_size=page_size, offset=offset))

    @app.get("/api/v1/runs/{run_id}/jobs/{job_id}", response_model=JobRecord, tags=["jobs"])
    async def get_run_job(request: Request, run_id: str = Path(min_length=1, max_length=128), job_id: str = Path(min_length=1, max_length=128)) -> JobRecord:
        read_principal(request, "jobs:read")
        return backend.get_job(run_id=run_id, job_id=job_id)

    @app.get("/api/v1/jobs/{job_id}", response_model=JobRecord, tags=["jobs"])
    async def get_job(request: Request, job_id: str = Path(min_length=1, max_length=128), run_id: str = Query(min_length=1, max_length=128)) -> JobRecord:
        read_principal(request, "jobs:read")
        return backend.get_job(run_id=run_id, job_id=job_id)

    @app.get("/api/v1/runs/{run_id}/artifacts", response_model=PageResponse, tags=["artifacts"])
    async def list_artifacts(
        request: Request,
        run_id: str = Path(min_length=1, max_length=128),
        stage_id: str | None = Query(default=None, max_length=128),
        artifact_kind: str | None = Query(default=None, max_length=128),
        page_size: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        read_principal(request, "artifacts:read")
        return _model_page(backend.list_artifacts(run_id=run_id, stage_id=stage_id, artifact_kind=artifact_kind, page_size=page_size, offset=offset), _artifact_view)

    @app.get("/api/v1/artifacts/{artifact_id}", response_model=ArtifactView, tags=["artifacts"])
    async def get_artifact(request: Request, artifact_id: str = Path(min_length=1, max_length=128), run_id: str = Query(min_length=1, max_length=128)) -> ArtifactView:
        read_principal(request, "artifacts:read")
        return _artifact_view(backend.artifact(run_id=run_id, artifact_id=artifact_id))

    @app.post("/api/v1/runs/{run_id}/artifacts/register", response_model=ArtifactView, status_code=201, tags=["artifacts"])
    async def register_artifact(payload: RegisterArtifactRequest, request: Request, run_id: str = Path(min_length=1, max_length=128)) -> ArtifactView:
        return _artifact_view(backend.register_artifact(run_id=run_id, artifact_id=payload.artifact_id, principal=principal(request)))

    @app.get("/api/v1/runs/{run_id}/artifacts/{artifact_id}/content", tags=["artifacts"])
    async def artifact_content(request: Request, run_id: str = Path(min_length=1, max_length=128), artifact_id: str = Path(min_length=1, max_length=128)) -> bytes:
        return backend.artifact_payload(run_id=run_id, artifact_id=artifact_id, principal=principal(request))

    @app.get("/api/v1/runs/{run_id}/reviews", response_model=PageResponse, tags=["reviews"])
    async def list_reviews(request: Request, run_id: str = Path(min_length=1, max_length=128), subject_key: str | None = Query(default=None, max_length=512), page_size: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)) -> dict[str, Any]:
        read_principal(request, "reviews:read")
        return _model_page(backend.list_reviews(run_id=run_id, subject_key=subject_key, page_size=page_size, offset=offset))

    @app.post("/api/v1/runs/{run_id}/reviews/{checkpoint}", tags=["reviews"])
    async def record_review(payload: ReviewActionRequest, request: Request, checkpoint: ReviewCheckpoint = Path(...), run_id: str = Path(min_length=1, max_length=128), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> JSONResponse:
        record, replayed = backend.review(run_id=run_id, checkpoint=checkpoint, context=payload.context, subject_artifact_id=payload.subject_artifact_id, subject_content_hash=payload.subject_content_hash, decision=payload.decision.value, rationale=payload.rationale, expected_revision=payload.expected_revision, principal=principal(request), idempotency_key=key(idempotency_key))
        response = JSONResponse(status_code=200, content=record.model_dump(mode="json"))
        response.headers["Idempotency-Replayed"] = "true" if replayed else "false"
        response.headers["ETag"] = f'"{record.revision}"'
        return response

    @app.post("/api/v1/runs/{run_id}/reviews/{checkpoint}/invalidate", tags=["reviews"])
    async def invalidate_review(payload: InvalidateReviewRequest, request: Request, checkpoint: ReviewCheckpoint = Path(...), run_id: str = Path(min_length=1, max_length=128), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> JSONResponse:
        record, replayed = backend.invalidate_review(run_id=run_id, checkpoint=checkpoint, context=payload.context, subject_artifact_id=payload.subject_artifact_id, subject_content_hash=payload.subject_content_hash, reason=payload.reason, expected_revision=payload.expected_revision, principal=principal(request), idempotency_key=key(idempotency_key))
        response = JSONResponse(status_code=200, content=record.model_dump(mode="json"))
        response.headers["Idempotency-Replayed"] = "true" if replayed else "false"
        response.headers["ETag"] = f'"{record.revision}"'
        return response

    @app.get("/api/v1/runs/{run_id}/validation/{artifact_id}", tags=["validation"])
    async def validation_view(request: Request, run_id: str = Path(min_length=1, max_length=128), artifact_id: str = Path(min_length=1, max_length=128), visualization_id: str = Query(default="validation-preview", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")) -> dict[str, Any]:
        read_principal(request, "validation:read")
        return backend.validation_view(run_id=run_id, artifact_id=artifact_id, visualization_id=visualization_id).model_dump(mode="json")

    @app.get("/api/v1/runs/{run_id}/visualizations/{artifact_id}", tags=["visualization"])
    async def visualization_view(request: Request, run_id: str = Path(min_length=1, max_length=128), artifact_id: str = Path(min_length=1, max_length=128)) -> dict[str, Any]:
        read_principal(request, "visualizations:read")
        return backend.visualization_graph(run_id=run_id, artifact_id=artifact_id).model_dump(mode="json")

    @app.post("/api/v1/runs/{run_id}/execution", tags=["execution"])
    async def submit_execution(request: Request, run_id: str = Path(min_length=1, max_length=128), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> JSONResponse:
        result, replayed = backend.submit(run_id=run_id, principal=principal(request), idempotency_key=key(idempotency_key))
        status = 503 if result.status in {"UNAVAILABLE", "DELIVERY_UNKNOWN"} else 202 if result.status == "ACCEPTED" else 409 if result.status == "CONFLICT" else 422
        response = JSONResponse(status_code=status, content=result.model_dump(mode="json"))
        response.headers["Idempotency-Replayed"] = "true" if replayed else "false"
        return response

    @app.post("/api/v1/runs/{run_id}/cancel", tags=["execution"])
    async def cancel_execution(request: Request, run_id: str = Path(min_length=1, max_length=128), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> JSONResponse:
        result, replayed = backend.cancel(run_id=run_id, principal=principal(request), idempotency_key=key(idempotency_key))
        status = 503 if result.status in {"UNAVAILABLE", "DELIVERY_UNKNOWN"} else 202 if result.status == "ACCEPTED" else 409
        response = JSONResponse(status_code=status, content=result.model_dump(mode="json"))
        response.headers["Idempotency-Replayed"] = "true" if replayed else "false"
        return response

    @app.post("/api/v1/runs/{run_id}/resume", tags=["execution"])
    async def resume_execution(request: Request, run_id: str = Path(min_length=1, max_length=128), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> JSONResponse:
        result, replayed = backend.resume(run_id=run_id, principal=principal(request), idempotency_key=key(idempotency_key))
        status = 503 if result.status in {"UNAVAILABLE", "DELIVERY_UNKNOWN"} else 202 if result.status == "ACCEPTED" else 409
        response = JSONResponse(status_code=status, content=result.model_dump(mode="json"))
        response.headers["Idempotency-Replayed"] = "true" if replayed else "false"
        return response

    return app


__all__ = [
    "ArtifactView",
    "CreateRunRequest",
    "ErrorEnvelope",
    "PageResponse",
    "RegisterArtifactRequest",
    "ReviewActionDecision",
    "RunView",
    "ReviewActionRequest",
    "create_app",
]
