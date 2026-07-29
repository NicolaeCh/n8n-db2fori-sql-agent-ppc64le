# Standalone n8n Db2 for i SQL Agent — ppc64le

A standalone, restricted SQL API for n8n workflows. The service runs in a rootless ppc64le container, connects to Db2 for i through Mapepire, and exposes a separate endpoint for each permitted operation.

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

Always use `?` parameters for values. Do not concatenate values received by n8n or another client into SQL text.

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
- Schema-qualified routine calls.
- Unqualified SQL functions absent from `SQL_ALLOWED_FUNCTIONS`.

`CREATE TABLE` must use an explicit parenthesized column list. CTAS, `LIKE`, foreign-key `REFERENCES`, reads inside the statement and trailing table options are rejected. Identity columns such as `GENERATED ALWAYS AS IDENTITY` are accepted.

Application validation is only one layer. The IBM i profile used by Mapepire must also have least-privilege object authority.

## Mapepire SQLJob pooling

The service maintains a local pool of reusable Mapepire `SQLJob` connections:

```dotenv
MAPEPIRE_POOL_ENABLED=true
MAPEPIRE_POOL_SIZE=4
MAPEPIRE_POOL_WAIT_SECONDS=30
MAPEPIRE_QUERY_TRACE_ENABLED=false
MAPEPIRE_SLOW_QUERY_MS=750
```

At startup it establishes up to four reusable jobs. Each API request borrows one job and returns it to the pool. A failed job is closed and replaced.

Write requests are not automatically retried after a transport failure because Db2 may already have committed the operation; an automatic retry could duplicate an insert or update.

Keep `WEB_CONCURRENCY=1` unless multiple independent pools are intentional. Approximate IBM i job count:

```text
WEB_CONCURRENCY × MAPEPIRE_POOL_SIZE
```

With pooling disabled, the service opens and closes one SQLJob per request.

## Correction for `ModuleNotFoundError: gssapi`

Mapepire 0.3.0 imports its Kerberos provider on Linux during package initialization. That provider imports `gssapi`, but `gssapi` is not declared as a normal Mapepire runtime dependency.

This project corrects that packaging gap in two places:

1. `requirements.txt` explicitly installs `gssapi`.
2. The multi-stage `Containerfile` builds the Python GSSAPI extension natively for ppc64le using `gcc`, `krb5-devel` and `python3.11-devel`.

Only `krb5-libs` and the compiled Python wheels are retained in the final image. The image build also runs this verification before completing:

```bash
python -c "import gssapi; from mapepire_python import SQLJob"
```

Rebuild without the previous cached dependency layer:

```bash
podman compose down
podman build --no-cache \
  --platform linux/ppc64le \
  -t localhost/n8n-db2-sql-agent-ppc64le:1.0.1 \
  -f Containerfile .
podman compose up -d --force-recreate
podman compose logs -f n8n-db2-sql-agent
```

If the build reaches `Mapepire and gssapi imports verified`, the import problem is resolved inside the image.

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
| `SQL_ALLOWED_READ_SCHEMAS` | Required | Comma-separated readable libraries/schemas. |
| `SQL_ALLOWED_WRITE_SCHEMA` | Required | Sole insert/update/create library. |
| `SQL_ALLOWED_FUNCTIONS` | Supplied list | Permitted unqualified SQL functions. |
| `SQL_MAX_SELECT_ROWS` | `1000` | Maximum rows returned to n8n. |
| `SQL_MAX_PARAMETERS` | `500` | Maximum placeholder/value count. |
| `SQL_MAX_LENGTH` | `65535` | Maximum SQL text length. |
| `MAPEPIRE_POOL_SIZE` | `4` | Reusable SQLJobs per process. |
| `MAPEPIRE_POOL_WAIT_SECONDS` | `30` | Wait for a free pooled job. |
| `MAPEPIRE_QUERY_TRACE_ENABLED` | `false` | Log a SQL hash instead of SQL text. |
| `MAPEPIRE_SLOW_QUERY_MS` | `750` | Slow-query warning threshold. |
| `WEB_CONCURRENCY` | `1` | Uvicorn processes; each has its own pool. |

## Build and run on IBM Power

```bash
podman build \
  --platform linux/ppc64le \
  -t localhost/n8n-db2-sql-agent-ppc64le:1.0.1 \
  -f Containerfile .

podman compose up -d
podman compose ps
podman compose logs n8n-db2-sql-agent
```

The compose file publishes the service on `127.0.0.1:8080` by default. To call it from n8n, attach both containers to the same Podman network and use:

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

## Importable n8n workflow with Brevo

The project includes:

```text
examples/n8n/select-rows-and-email-with-brevo.workflow.json
```

The workflow performs this sequence:

```text
Manual Trigger
  → HTTP Request to /api/v1/sql/select
  → Code node that converts returned rows to an HTML table
  → Brevo node that sends the table by email
```

After import:

1. In **Select rows from IBM i**, select an n8n Header Auth credential with header name `X-API-Key` and the same value as `DB2_API_KEY`.
2. Replace `MYLIB.CUSTOMERS` and its columns with a fully qualified table allowed by `SQL_ALLOWED_READ_SCHEMAS`.
3. In **Send email with Brevo**, select a Brevo API credential.
4. Replace the example sender with an address authorized in Brevo and set the recipient.
5. Execute manually, or replace the Manual Trigger with a Schedule Trigger.

No secret is embedded in the workflow JSON.

## API examples

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
  "sql": "INSERT INTO APPDATA.EVENT_AUDIT (EVENT_ID, STATUS) VALUES (?, ?)",
  "parameters": ["{{$json.event_id}}", "RECEIVED"]
}
```

### Update

```json
{
  "sql": "UPDATE APPDATA.EVENT_AUDIT SET STATUS=? WHERE EVENT_ID=?",
  "parameters": ["PROCESSED", "{{$json.event_id}}"]
}
```

### Create table

```json
{
  "sql": "CREATE TABLE APPDATA.AGENT_TEST (ID BIGINT GENERATED ALWAYS AS IDENTITY, NAME VARCHAR(100), PRIMARY KEY (ID))",
  "parameters": []
}
```

Swagger is available at `/docs` when `API_DOCS_ENABLED=true`.

## IBM i authority design

Use a dedicated profile such as `N8NDB2`, with no special authorities. Grant only:

- Read authority to required objects in configured read libraries.
- `*ADD` and `*UPD` only on required write objects.
- Library `*ADD` and minimum object-management authority only when table creation is required.
- No authority to unrelated libraries, commands or administrative services.

For maximum separation, deploy two instances: a normal runtime instance whose profile cannot create objects, and a provisioning instance enabled only during deployment.

## Testing

The policy and pool tests do not require IBM i:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
PYTHONPATH=. pytest -q
```

Validate the workflow JSON:

```bash
python -m json.tool examples/n8n/select-rows-and-email-with-brevo.workflow.json >/dev/null
```

After deployment, run `/health/db2` and test against a disposable table in `SQL_ALLOWED_WRITE_SCHEMA`.

## Project files

```text
app/main.py                 FastAPI endpoints, authentication and responses
app/sql_policy.py           Deny-by-default SQL validation
app/mapepire_pool.py        Reusable SQLJob connection pool
app/settings.py             Environment configuration
Containerfile               Multi-stage native ppc64le image
compose.yaml                Hardened Podman Compose service
examples/n8n/               Request bodies and importable workflow
ARCHITECTURE.mmd             Overall Mermaid architecture
SECURITY_FLOW.mmd            SQL request validation flow
tests/                       Offline security and pool tests
```
