from __future__ import annotations

import contextlib
import dataclasses
import json
import logging
import queue
import threading
from collections.abc import Iterator
from typing import Any, Callable

from .settings import Settings

logger = logging.getLogger(__name__)


def _import_sql_job():
    try:
        from mapepire_python import SQLJob  # type: ignore
        return SQLJob
    except (ImportError, AttributeError):
        from mapepire_python.client.sql_job import SQLJob  # type: ignore
        return SQLJob


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {key: _jsonable(item) for key, item in dataclasses.asdict(value).items()}
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _jsonable(value.to_dict())
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


class PoolUnavailable(RuntimeError):
    pass


class MapepireJobPool:
    def __init__(self, settings: Settings, job_factory: Callable[..., Any] | None = None):
        self.settings = settings
        self._job_factory = job_factory or _import_sql_job()
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=settings.mapepire_pool_size)
        self._lock = threading.Lock()
        self._created = 0
        self._closed = False
        self._startup_errors = 0

    def _new_job(self) -> Any:
        credentials = self.settings.mapepire_credentials
        job = self._job_factory(credentials)
        job.connect(credentials)
        with self._lock:
            self._created += 1
        return job

    def start(self) -> None:
        if not self.settings.mapepire_pool_enabled:
            logger.info("Mapepire pool disabled; a SQLJob will be opened per request")
            return
        for _ in range(self.settings.mapepire_pool_size):
            try:
                self._queue.put_nowait(self._new_job())
            except Exception as exc:
                self._startup_errors += 1
                logger.error("Unable to pre-create Mapepire SQLJob: %s", exc)
                continue
        logger.info(
            "Mapepire pool initialized: available=%s configured=%s",
            self._queue.qsize(),
            self.settings.mapepire_pool_size,
        )

    def close(self) -> None:
        self._closed = True
        while True:
            try:
                job = self._queue.get_nowait()
            except queue.Empty:
                break
            self._safe_close(job)
            with self._lock:
                self._created = max(0, self._created - 1)

    @staticmethod
    def _safe_close(job: Any) -> None:
        try:
            job.close()
        except Exception:
            logger.debug("Ignoring error while closing Mapepire SQLJob", exc_info=True)

    def _replace_broken(self, broken_job: Any) -> None:
        self._safe_close(broken_job)
        with self._lock:
            self._created = max(0, self._created - 1)
        if self._closed or not self.settings.mapepire_pool_enabled:
            return
        try:
            replacement = self._new_job()
            self._queue.put_nowait(replacement)
        except Exception as exc:
            logger.error("Unable to replace failed Mapepire SQLJob: %s", exc)

    @contextlib.contextmanager
    def acquire(self) -> Iterator[Any]:
        if self._closed:
            raise PoolUnavailable("Mapepire pool is closed")
        if not self.settings.mapepire_pool_enabled:
            job = self._new_job()
            try:
                yield job
            finally:
                self._safe_close(job)
                with self._lock:
                    self._created = max(0, self._created - 1)
            return

        if self._queue.empty():
            with self._lock:
                may_create = self._created < self.settings.mapepire_pool_size
            if may_create:
                try:
                    self._queue.put_nowait(self._new_job())
                except Exception as exc:
                    logger.error("Unable to create Mapepire SQLJob on demand: %s", exc)

        try:
            job = self._queue.get(timeout=self.settings.mapepire_pool_wait_seconds)
        except queue.Empty as exc:
            raise PoolUnavailable(
                f"No Mapepire SQLJob became available within {self.settings.mapepire_pool_wait_seconds} seconds"
            ) from exc

        broken = False
        try:
            yield job
        except Exception:
            broken = True
            raise
        finally:
            if broken:
                self._replace_broken(job)
            elif not self._closed:
                try:
                    self._queue.put_nowait(job)
                except queue.Full:
                    self._safe_close(job)
                    with self._lock:
                        self._created = max(0, self._created - 1)

    def execute(self, sql: str, parameters: list[Any], rows_to_fetch: int) -> dict[str, Any]:
        opts = {
            "isClCommand": False,
            "parameters": parameters if parameters else None,
            "autoClose": False,
        }
        with self.acquire() as job:
            with job.query(sql, opts) as query:
                result = query.run(rows_to_fetch=rows_to_fetch)
        normalized = _jsonable(result)
        if not isinstance(normalized, dict):
            raise RuntimeError("Unexpected Mapepire result type")
        return normalized

    def stats(self) -> dict[str, Any]:
        with self._lock:
            created = self._created
        return {
            "enabled": self.settings.mapepire_pool_enabled,
            "configured_size": self.settings.mapepire_pool_size,
            "created": created,
            "available": self._queue.qsize() if self.settings.mapepire_pool_enabled else None,
            "wait_seconds": self.settings.mapepire_pool_wait_seconds,
            "startup_errors": self._startup_errors,
        }
