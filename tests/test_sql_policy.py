import pytest

from app.sql_policy import SQLPolicyError, validate_sql

READ = ["QSYS2", "SYSTOOLS", "MONAI"]
WRITE = "MONAI"
FUNCTIONS = ["COUNT", "SUM", "COALESCE", "UPPER", "VARCHAR", "CURRENT_TIMESTAMP"]


def valid(sql, op, params=0):
    return validate_sql(sql, op, READ, WRITE, FUNCTIONS, parameter_values_count=params)


def test_select_qualified_allowed():
    result = valid("SELECT JOB_NAME FROM QSYS2.ACTIVE_JOB_INFO", "select")
    assert result.read_schemas == ("QSYS2",)


def test_select_join_and_subquery_schemas_checked():
    result = valid(
        "SELECT A.ID FROM MONAI.A A JOIN QSYS2.B B ON A.ID=B.ID "
        "WHERE EXISTS (SELECT 1 FROM SYSTOOLS.C C WHERE C.ID=A.ID)",
        "select",
    )
    assert set(result.read_schemas) == {"MONAI", "QSYS2", "SYSTOOLS"}


def test_select_unqualified_rejected():
    with pytest.raises(SQLPolicyError, match="Unqualified"):
        valid("SELECT * FROM ACTIVE_JOB_INFO", "select")


def test_select_denied_schema_rejected():
    with pytest.raises(SQLPolicyError, match="not allowed"):
        valid("SELECT * FROM SECRET.PAYROLL", "select")


def test_multiple_statements_rejected():
    with pytest.raises(SQLPolicyError, match="Multiple"):
        valid("SELECT * FROM QSYS2.ACTIVE_JOB_INFO; DROP TABLE MONAI.X", "select")


def test_one_trailing_semicolon_is_accepted():
    result = valid("SELECT * FROM QSYS2.ACTIVE_JOB_INFO;", "select")
    assert not result.sql.endswith(";")


def test_comments_rejected():
    with pytest.raises(SQLPolicyError, match="comments"):
        valid("SELECT * FROM QSYS2.ACTIVE_JOB_INFO -- hidden", "select")


def test_declared_operation_mismatch_rejected():
    with pytest.raises(SQLPolicyError, match="does not match"):
        valid("UPDATE MONAI.T SET C=?", "select", 1)


def test_call_grant_revoke_rejected():
    statements = [
        "CALL QSYS2.X()",
        "GRANT SELECT ON MONAI.T TO USER X",
        "REVOKE SELECT ON MONAI.T FROM USER X",
    ]
    for statement in statements:
        with pytest.raises(SQLPolicyError):
            valid(statement, "select")


def test_insert_write_schema_allowed():
    result = valid("INSERT INTO MONAI.ALERTS (ID, TXT) VALUES (?, ?)", "insert", 2)
    assert result.write_schema == "MONAI"


def test_insert_wrong_schema_rejected():
    with pytest.raises(SQLPolicyError, match="only in schema"):
        valid("INSERT INTO OTHER.ALERTS (ID) VALUES (?)", "insert", 1)


def test_insert_select_read_schema_checked():
    with pytest.raises(SQLPolicyError, match="Read schema"):
        valid("INSERT INTO MONAI.T (ID) SELECT ID FROM SECRET.T", "insert")


def test_update_requires_qualified_target_and_set():
    result = valid("UPDATE MONAI.ALERTS SET STATUS=? WHERE ID=?", "update", 2)
    assert result.write_schema == "MONAI"
    with pytest.raises(SQLPolicyError):
        valid("UPDATE ALERTS SET STATUS=?", "update", 1)


def test_create_table_allowed_in_write_schema():
    result = valid(
        "CREATE TABLE MONAI.AGENT_TEST ("
        "ID BIGINT GENERATED ALWAYS AS IDENTITY, NAME VARCHAR(100), PRIMARY KEY (ID))",
        "create_table",
    )
    assert result.write_schema == "MONAI"


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE VIEW MONAI.V AS SELECT * FROM MONAI.T",
        "CREATE TABLE OTHER.T (ID INTEGER)",
        "CREATE TABLE MONAI.T LIKE OTHER.T",
        "CREATE TABLE MONAI.T AS (SELECT * FROM QSYS2.ACTIVE_JOB_INFO) WITH DATA",
        "CREATE TABLE MONAI.T (ID INTEGER REFERENCES OTHER.T(ID))",
    ],
)
def test_create_table_restricted_forms_rejected(sql):
    with pytest.raises(SQLPolicyError):
        valid(sql, "create_table")


def test_parameter_count_must_match():
    with pytest.raises(SQLPolicyError, match="Parameter count mismatch"):
        valid("SELECT * FROM QSYS2.ACTIVE_JOB_INFO WHERE JOB_NAME=?", "select", 0)


def test_keyword_inside_string_is_not_a_second_statement():
    result = valid("SELECT * FROM MONAI.T WHERE TXT='CALL GRANT REVOKE'", "select")
    assert result.operation == "select"


def test_comma_separated_sources_are_checked():
    with pytest.raises(SQLPolicyError, match="Read schema"):
        valid("SELECT * FROM MONAI.A A, SECRET.B B WHERE A.ID=B.ID", "select")


def test_allowlisted_function_is_accepted():
    result = valid("SELECT COUNT(*) AS N FROM MONAI.T", "select")
    assert result.operation == "select"


def test_unallowlisted_and_schema_qualified_routines_are_rejected():
    with pytest.raises(SQLPolicyError, match="not in SQL_ALLOWED_FUNCTIONS"):
        valid("SELECT QCMDEXC('DSPJOB') FROM QSYS2.SYSDUMMY1", "select")
    with pytest.raises(SQLPolicyError, match="Schema-qualified routine"):
        valid("SELECT QSYS2.QCMDEXC('DSPJOB') FROM QSYS2.SYSDUMMY1", "select")
