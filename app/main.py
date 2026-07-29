from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from .mapepire_pool import MapepireJobPool, PoolUnavailable
from .models import HealthResponse, SQLRequest, SQLResponse
from .settings import get_settings
from .sql_policy import SQLPolicyError, ValidatedSQL, validate_sql

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("n8n_db2_sql_agent")
pool = MapepireJobPool(settings)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    pool.start()
    yield
    pool.close()


app = FastAPI(
    title="n8n Db2 for i Restricted SQL Agent",
    version=settings.app_version,
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
    lifespan=lifespan,
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))[:128]
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(SQLPolicyError)
async def sql_policy_error_handler(request: Request, exc: SQLPolicyError):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "ok": False,
            "error": "sql_policy_rejected",
            "detail": str(exc),
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.exception_handler(PoolUnavailable)
async def pool_error_handler(request: Request, exc: PoolUnavailable):
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "ok": False,
            "error": "mapepire_pool_unavailable",
            "detail": str(exc),
            "request_id": getattr(request.state, "request_id", None),
        },
    )


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    if x_api_key is None or not hmac.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )


def _validate(body: SQLRequest, operation: str) -> ValidatedSQL:
    return validate_sql(
        body.sql,
        declared_operation=operation,
        allowed_read_schemas=settings.allowed_read_schemas,
        allowed_write_schema=settings.allowed_write_schema,
        allowed_functions=settings.allowed_functions,
        max_sql_length=settings.max_sql_length,
        max_parameters=settings.max_parameters,
        parameter_values_count=len(body.parameters),
    )


def _execute(request: Request, body: SQLRequest, operation: str) -> SQLResponse:
    validated = _validate(body, operation)
    request_id = request.state.request_id
    sql_hash = hashlib.sha256(validated.sql.encode("utf-8")).hexdigest()[:16]
    fetch_rows = settings.max_select_rows + 1 if operation == "select" else 1

    started = time.perf_counter()
    try:
        result = pool.execute(validated.sql, body.parameters, rows_to_fetch=fetch_rows)
    except PoolUnavailable:
        raise
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.exception(
            "Db2 execution failed operation=%s request_id=%s sql_hash=%s elapsed_ms=%s",
            operation,
            request_id,
            sql_hash,
            elapsed_ms,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "db2_sql_execution_failed",
                "message": str(exc),
                "request_id": request_id,
            },
        ) from exc

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    data = result.get("data") or []
    if not isinstance(data, list):
        data = [data]
    truncated = operation == "select" and (
        len(data) > settings.max_select_rows or result.get("is_done") is False
    )
    if operation == "select":
        data = data[: settings.max_select_rows]

    if settings.mapepire_query_trace_enabled:
        logger.info(
            "SQL executed operation=%s request_id=%s elapsed_ms=%s sql=%r params=%s",
            operation,
            request_id,
            elapsed_ms,
            validated.sql,
            len(body.parameters),
        )
    else:
        logger.info(
            "SQL executed operation=%s request_id=%s elapsed_ms=%s sql_hash=%s params=%s",
            operation,
            request_id,
            elapsed_ms,
            sql_hash,
            len(body.parameters),
        )
    if elapsed_ms >= settings.mapepire_slow_query_ms:
        logger.warning(
            "Slow Db2 query operation=%s request_id=%s elapsed_ms=%s threshold_ms=%s sql_hash=%s",
            operation,
            request_id,
            elapsed_ms,
            settings.mapepire_slow_query_ms,
            sql_hash,
        )

    update_count = result.get("update_count")
    if update_count is not None:
        try:
            update_count = int(update_count)
        except (TypeError, ValueError):
            update_count = None

    metadata = result.get("metadata")
    return SQLResponse(
        operation=operation,
        request_id=request_id,
        elapsed_ms=elapsed_ms,
        update_count=update_count,
        row_count=len(data),
        truncated=truncated,
        data=data,
        metadata=metadata if isinstance(metadata, dict) else None,
    )


@app.get("/health/live", response_model=HealthResponse, tags=["health"])
def health_live() -> HealthResponse:
    return HealthResponse(ok=True, service=settings.app_name, version=settings.app_version)


@app.get("/health/ready", response_model=HealthResponse, tags=["health"])
def health_ready() -> HealthResponse:
    stats = pool.stats()
    ready = not settings.mapepire_pool_enabled or int(stats.get("available") or 0) > 0
    return HealthResponse(
        ok=ready,
        service=settings.app_name,
        version=settings.app_version,
        pool=stats,
        detail=None if ready else "No reusable Mapepire SQLJob is currently available",
    )


@app.get(
    "/health/db2",
    response_model=HealthResponse,
    dependencies=[Depends(require_api_key)],
    tags=["health"],
)
def health_db2(request: Request) -> HealthResponse:
    started = time.perf_counter()
    try:
        result = pool.execute("VALUES CURRENT SERVER", [], rows_to_fetch=1)
    except Exception as exc:
        logger.exception("Deep Db2 health check failed")
        raise HTTPException(status_code=503, detail=f"Db2 connectivity failed: {exc}") from exc
    data = result.get("data") or []
    server = None
    if data and isinstance(data[0], dict):
        server = str(next(iter(data[0].values()), "")) or None
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return HealthResponse(
        ok=True,
        service=settings.app_name,
        version=settings.app_version,
        pool=pool.stats(),
        db2_server=server,
        detail=f"Db2 responded in {elapsed_ms} ms; request_id={request.state.request_id}",
    )


@app.post("/api/v1/sql/select", response_model=SQLResponse, dependencies=[Depends(require_api_key)], tags=["sql"])
def sql_select(request: Request, body: SQLRequest) -> SQLResponse:
    return _execute(request, body, "select")


@app.post("/api/v1/sql/insert", response_model=SQLResponse, dependencies=[Depends(require_api_key)], tags=["sql"])
def sql_insert(request: Request, body: SQLRequest) -> SQLResponse:
    return _execute(request, body, "insert")


@app.post("/api/v1/sql/update", response_model=SQLResponse, dependencies=[Depends(require_api_key)], tags=["sql"])
def sql_update(request: Request, body: SQLRequest) -> SQLResponse:
    return _execute(request, body, "update")


@app.post("/api/v1/sql/create-table", response_model=SQLResponse, dependencies=[Depends(require_api_key)], tags=["sql"])
def sql_create_table(request: Request, body: SQLRequest) -> SQLResponse:
    return _execute(request, body, "create_table")
