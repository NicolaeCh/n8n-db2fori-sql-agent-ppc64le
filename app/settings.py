from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = int(raw)
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _csv(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    values: list[str] = []
    for item in raw.split(","):
        item = item.strip().upper()
        if item and item not in values:
            values.append(item)
    return tuple(values)


@dataclass(frozen=True)
class Settings:
    app_name: str
    app_version: str
    log_level: str
    api_key: str
    docs_enabled: bool
    mapepire_host: str
    mapepire_port: int
    mapepire_user: str
    mapepire_password: str
    mapepire_tls_verify: bool
    mapepire_ca_path: str | None
    mapepire_pool_enabled: bool
    mapepire_pool_size: int
    mapepire_pool_wait_seconds: int
    mapepire_query_trace_enabled: bool
    mapepire_slow_query_ms: int
    allowed_read_schemas: tuple[str, ...]
    allowed_write_schema: str
    allowed_functions: tuple[str, ...]
    max_sql_length: int
    max_parameters: int
    max_select_rows: int

    @classmethod
    def from_env(cls) -> "Settings":
        api_key = os.getenv("DB2_API_KEY", os.getenv("SQL_AGENT_API_KEY", "")).strip()
        host = os.getenv("MAPEPIRE_HOST", "").strip()
        user = os.getenv("MAPEPIRE_USER", "").strip()
        password = os.getenv("MAPEPIRE_PASSWORD", "")
        read_schemas = _csv("SQL_ALLOWED_READ_SCHEMAS")
        write_schema = os.getenv("SQL_ALLOWED_WRITE_SCHEMA", "").strip().upper()

        required = {
            "DB2_API_KEY": api_key,
            "MAPEPIRE_HOST": host,
            "MAPEPIRE_USER": user,
            "MAPEPIRE_PASSWORD": password,
            "SQL_ALLOWED_READ_SCHEMAS": read_schemas,
            "SQL_ALLOWED_WRITE_SCHEMA": write_schema,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError("Missing required environment variables: " + ", ".join(missing))

        return cls(
            app_name=os.getenv("APP_NAME", "n8n-db2-sql-agent"),
            app_version=os.getenv("APP_VERSION", "1.0.0"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            api_key=api_key,
            docs_enabled=_bool("API_DOCS_ENABLED", True),
            mapepire_host=host,
            mapepire_port=_int("MAPEPIRE_PORT", 8076, 1),
            mapepire_user=user,
            mapepire_password=password,
            mapepire_tls_verify=_bool("MAPEPIRE_TLS_VERIFY", True),
            mapepire_ca_path=(os.getenv("MAPEPIRE_CA_PATH") or "").strip() or None,
            mapepire_pool_enabled=_bool("MAPEPIRE_POOL_ENABLED", True),
            mapepire_pool_size=_int("MAPEPIRE_POOL_SIZE", 4, 1),
            mapepire_pool_wait_seconds=_int("MAPEPIRE_POOL_WAIT_SECONDS", 30, 1),
            mapepire_query_trace_enabled=_bool("MAPEPIRE_QUERY_TRACE_ENABLED", False),
            mapepire_slow_query_ms=_int("MAPEPIRE_SLOW_QUERY_MS", 750, 0),
            allowed_read_schemas=read_schemas,
            allowed_write_schema=write_schema,
            allowed_functions=_csv("SQL_ALLOWED_FUNCTIONS"),
            max_sql_length=_int("SQL_MAX_LENGTH", 65535, 128),
            max_parameters=_int("SQL_MAX_PARAMETERS", 500, 0),
            max_select_rows=_int("SQL_MAX_SELECT_ROWS", 1000, 1),
        )

    @property
    def mapepire_credentials(self) -> dict[str, object]:
        credentials: dict[str, object] = {
            "host": self.mapepire_host,
            "port": self.mapepire_port,
            "user": self.mapepire_user,
            "password": self.mapepire_password,
            "ignoreUnauthorized": not self.mapepire_tls_verify,
        }
        if self.mapepire_ca_path:
            with open(self.mapepire_ca_path, "r", encoding="utf-8") as ca_file:
                credentials["ca"] = ca_file.read()
        return credentials


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
