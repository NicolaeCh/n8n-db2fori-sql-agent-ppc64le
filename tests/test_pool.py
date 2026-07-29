from app.mapepire_pool import MapepireJobPool
from app.settings import Settings


class FakeQuery:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def run(self, rows_to_fetch=1):
        return {"data": [{"OK": 1}], "update_count": -1, "is_done": True}


class FakeJob:
    def __init__(self, creds):
        self.creds = creds
        self.connected = False
        self.closed = False

    def connect(self, creds):
        self.connected = True

    def close(self):
        self.closed = True

    def query(self, sql, opts):
        return FakeQuery()


def settings():
    return Settings(
        app_name="test",
        app_version="1",
        log_level="INFO",
        api_key="secret",
        docs_enabled=False,
        mapepire_host="ibmi",
        mapepire_port=8076,
        mapepire_user="u",
        mapepire_password="p",
        mapepire_tls_verify=True,
        mapepire_ca_path=None,
        mapepire_pool_enabled=True,
        mapepire_pool_size=2,
        mapepire_pool_wait_seconds=1,
        mapepire_query_trace_enabled=False,
        mapepire_slow_query_ms=750,
        allowed_read_schemas=("QSYS2",),
        allowed_write_schema="APPDATA",
        allowed_functions=("COUNT",),
        max_sql_length=1000,
        max_parameters=10,
        max_select_rows=100,
    )


def test_pool_reuses_jobs():
    pool = MapepireJobPool(settings(), job_factory=FakeJob)
    pool.start()
    assert pool.stats()["available"] == 2
    first = pool.execute("VALUES 1", [], 1)
    second = pool.execute("VALUES 1", [], 1)
    assert first["data"][0]["OK"] == 1
    assert second["data"][0]["OK"] == 1
    assert pool.stats()["created"] == 2
    pool.close()
