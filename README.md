# Standalone n8n Db2 for i SQL Agent — ppc64le

This project extracts the Db2/Mapepire capability from the Grafana+n8n monitoring solution into an independent API service. It runs as a rootless ppc64le container and can be called by one or more n8n workflows.

## Published APIs

| Purpose | Endpoint | Accepted first verb |
|---|---|---|
| Read | `POST /api/v1/sql/select` | `SELECT` |
| Insert | `POST /api/v1/sql/insert` | `INSERT` |
| Update | `POST /api/v1/sql/update` | `UPDATE` |
| Create a base table | `POST /api/v1/sql/create-table` | `CREATE TABLE` |

All four endpoints require:

```http
X-API-Key: <DB2_API_KEY from .env>
Content-Type: application/json
```

Request body:

```json
{
  "sql": "SELECT JOB_NAME FROM QSYS2.ACTIVE_JOB_INFO WHERE JOB_NAME = ?",
  "parameters": ["123456/USER/JOB"]
}
```

Use `?` placeholders. Do not concatenate Grafana fields, webhook values or other untrusted data into SQL text.

## Security policy

The validator is deny-by-default and rejects:

- Multiple statements. One optional trailing semicolon is accepted.
- SQL comments.
- DDL other than restricted `CREATE TABLE` on its dedicated endpoint.
- `CALL`, `GRANT`, `REVOKE`, dynamic SQL, transaction control, procedures, triggers, views, indexes, aliases and similar constructs.
- A mismatch between the endpoint operation and the first SQL verb.
- Unqualified table names in `FROM`, `JOIN`, `INSERT INTO`, `UPDATE` and `CREATE TABLE`.
- Reads from schemas absent from `SQL_ALLOWED_READ_SCHEMAS`.
- Inserts, updates or table creation outside `SQL_ALLOWED_WRITE_SCHEMA`.
- A mismatch between `?` placeholders and supplied parameters.
- Schema-qualified routine calls. This blocks patterns such as `QSYS2.QCMDEXC(...)` that could turn a nominal read into an action.
- Unqualified SQL functions absent from `SQL_ALLOWED_FUNCTIONS`.

`CREATE TABLE` must use an explicit parenthesized column list. CTAS, `LIKE`, foreign-key `REFERENCES`, reads inside the statement and trailing table options are rejected. Identity columns such as `GENERATED ALWAYS AS IDENTITY` are accepted.

Application validation is only one layer. The IBM i profile used by Mapepire must also have least-privilege object authority.

## Mapepire SQLJob pooling

The service implements the requested local SQLJob pool:

```dotenv
MAPEPIRE_POOL_ENABLED=true
MAPEPIRE_POOL_SIZE=4
MAPEPIRE_POOL_WAIT_SECONDS=30
MAPEPIRE_QUERY_TRACE_ENABLED=false
MAPEPIRE_SLOW_QUERY_MS=750
```

At startup it tries to establish four reusable Mapepire SQLJob connections. Each request borrows one job and returns it to the local pool. A failed job is closed and replaced.

Write requests are not automatically retried after a transport failure because Db2 may already have committed the operation; automatic retry could duplicate an insert or update.

Keep `WEB_CONCURRENCY=1` unless multiple independent pools are intentional. Approximate IBM i job count:

```text
WEB_CONCURRENCY × MAPEPIRE_POOL_SIZE
```

With pooling disabled, the service opens and closes one SQLJob per request, matching the previous behavior.

## Environment configuration

Copy and edit the supplied file:

```bash
cp .env.example .env
vi .env
```

Important variables:

| Variable | Default | Purpose |
|---|---:|---|
| `DB2_API_KEY` | Required | Expected `X-API-Key`. Generate a long random value. |
| `MAPEPIRE_HOST` | Required | IBM i host running Mapepire Server. |
| `MAPEPIRE_PORT` | `8076` | Mapepire port. |
| `MAPEPIRE_USER` | Required | Dedicated IBM i profile. |
| `MAPEPIRE_PASSWORD` | Required | Profile password. |
| `MAPEPIRE_TLS_VERIFY` | `true` | Verify TLS. |
| `MAPEPIRE_CA_PATH` | Empty | Optional PEM CA file mounted in the container. |
| `SQL_ALLOWED_READ_SCHEMAS` | Required | Comma-separated read libraries/schemas. |
| `SQL_ALLOWED_WRITE_SCHEMA` | Required | The sole insert/update/create library. |
| `SQL_ALLOWED_FUNCTIONS` | Supplied list | Permitted unqualified SQL functions. |
| `SQL_MAX_SELECT_ROWS` | `1000` | Maximum rows returned to n8n. |
| `SQL_MAX_PARAMETERS` | `500` | Maximum placeholder/value count. |
| `SQL_MAX_LENGTH` | `65535` | Maximum SQL text length. |
| `MAPEPIRE_POOL_SIZE` | `4` | Reusable SQLJobs per process. |
| `MAPEPIRE_POOL_WAIT_SECONDS` | `30` | Wait for a free pooled job. |
| `MAPEPIRE_QUERY_TRACE_ENABLED` | `false` | When false, logs a SQL hash rather than SQL text. |
| `MAPEPIRE_SLOW_QUERY_MS` | `750` | Slow-query warning threshold. |
| `WEB_CONCURRENCY` | `1` | Uvicorn processes; each has its own pool. |

## Build and run on IBM Power

The Containerfile uses Red Hat UBI 9 Python 3.11 and contains only Python dependencies, making it suitable for native ppc64le builds.

```bash
podman build \
  --platform linux/ppc64le \
  -t localhost/n8n-db2-sql-agent-ppc64le:1.0.0 \
  -f Containerfile .

podman compose up -d
podman compose ps
podman logs n8n-db2-sql-agent-ppc64le_n8n-db2-sql-agent_1
```

The exact Podman-generated container name can differ. The compose file publishes the service on `127.0.0.1:8080` by default. For container-to-container communication, attach this service and n8n to the same Podman network and call:

```text
http://n8n-db2-sql-agent:8080
```

The container runs as UID 1001, drops all Linux capabilities, enables `no-new-privileges`, uses a read-only filesystem and provides only a small `/tmp` tmpfs.

## Health endpoints

| Endpoint | API key | Purpose |
|---|---|---|
| `GET /health/live` | No | Process liveness. |
| `GET /health/ready` | No | Local pool availability. |
| `GET /health/db2` | Yes | Executes internal `VALUES CURRENT SERVER`. |

Example:

```bash
curl -sS http://127.0.0.1:8080/health/db2 \
  -H "X-API-Key: $DB2_API_KEY"
```

## n8n HTTP Request nodes

Create one HTTP Request node per operation. Use the endpoint matching the SQL verb.

Common configuration:

```text
Method: POST
URL: http://n8n-db2-sql-agent:8080/api/v1/sql/<operation>
Header: X-API-Key = stored n8n credential/secret
Send Body: JSON
```

### Select

```json
{
  "sql": "SELECT JOB_NAME, JOB_STATUS FROM QSYS2.ACTIVE_JOB_INFO FETCH FIRST 10 ROWS ONLY",
  "parameters": []
}
```

### Insert

```json
{
  "sql": "INSERT INTO MONAI.ALERT_AUDIT (ALERT_ID, STATUS) VALUES (?, ?)",
  "parameters": ["{{$json.alert_id}}", "RECEIVED"]
}
```

### Update

```json
{
  "sql": "UPDATE MONAI.ALERT_AUDIT SET STATUS=? WHERE ALERT_ID=?",
  "parameters": ["PROCESSED", "{{$json.alert_id}}"]
}
```

### Create table

```json
{
  "sql": "CREATE TABLE MONAI.AGENT_TEST (ID BIGINT GENERATED ALWAYS AS IDENTITY, NAME VARCHAR(100), PRIMARY KEY (ID))",
  "parameters": []
}
```

Swagger is available at `/docs` when `API_DOCS_ENABLED=true`.

## IBM i authority design

Use a dedicated profile such as `N8NDB2`, with no special authorities. Grant only:

- Read authority to objects in the configured read libraries.
- `*ADD` and `*UPD` only on the required write objects.
- Library `*ADD` and the minimum object-management authority only when table creation is genuinely required.
- No authority to unrelated libraries, CL commands or administrative services.

For maximum separation, deploy two instances: a normal runtime instance whose IBM i profile cannot create objects, and a provisioning instance that is enabled only during deployment. The supplied project implements all four requested APIs in one service, while IBM i object authority remains the final enforcement layer.

## Testing

The policy and pool tests do not require IBM i:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
PYTHONPATH=. pytest -q
```

Current package test result: 25 tests passed.

After deployment, run `/health/db2` and then test against a disposable table in `SQL_ALLOWED_WRITE_SCHEMA`.

## Project files

```text
app/main.py              FastAPI endpoints, authentication and response handling
app/sql_policy.py        SQL tokenizer and deny-by-default policy
app/mapepire_pool.py     Reusable SQLJob connection pool
app/settings.py          .env configuration
Containerfile            Native ppc64le image
compose.yaml             Hardened Podman Compose service
examples/n8n/            Request bodies for each endpoint
ARCHITECTURE.mmd          Overall Mermaid architecture
SECURITY_FLOW.mmd         SQL request validation flow
tests/                    Offline security and pooling tests
```
