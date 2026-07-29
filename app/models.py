from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field, validator


class SQLRequest(BaseModel):
    sql: str = Field(..., min_length=1)
    parameters: list[Any] = Field(default_factory=list)

    @validator("sql")
    def sql_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("sql must not be blank")
        return value


class SQLResponse(BaseModel):
    ok: bool = True
    operation: str
    request_id: str
    elapsed_ms: int
    update_count: int | None = None
    row_count: int = 0
    truncated: bool = False
    data: list[Any] = Field(default_factory=list)
    metadata: dict[str, Any] | None = None


class HealthResponse(BaseModel):
    ok: bool
    service: str
    version: str
    pool: dict[str, Any] | None = None
    db2_server: str | None = None
    detail: str | None = None
