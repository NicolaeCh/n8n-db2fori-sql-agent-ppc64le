from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class SQLPolicyError(ValueError):
    """Raised when SQL does not satisfy the service policy."""


class TokenKind(str, Enum):
    WORD = "WORD"
    QIDENT = "QIDENT"
    STRING = "STRING"
    NUMBER = "NUMBER"
    DOT = "DOT"
    COMMA = "COMMA"
    LPAREN = "LPAREN"
    RPAREN = "RPAREN"
    PARAM = "PARAM"
    SEMICOLON = "SEMICOLON"
    OP = "OP"


@dataclass(frozen=True)
class Token:
    kind: TokenKind
    value: str
    position: int

    @property
    def upper(self) -> str:
        return self.value.upper()


@dataclass(frozen=True)
class ValidatedSQL:
    sql: str
    operation: str
    read_schemas: tuple[str, ...]
    write_schema: str | None
    parameter_count: int


CLAUSE_END = {
    "WHERE", "GROUP", "HAVING", "ORDER", "FETCH", "OFFSET", "UNION",
    "EXCEPT", "INTERSECT", "QUALIFY", "FOR", "LIMIT", "CONNECT", "START",
}
FORBIDDEN_ANYWHERE = {
    "CALL", "GRANT", "REVOKE", "ALTER", "DROP", "TRUNCATE", "RENAME",
    "COMMENT", "LABEL", "TRANSFER", "AUDIT", "NOAUDIT", "EXECUTE",
    "PREPARE", "DECLARE", "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT",
    "RELEASE", "SIGNAL", "RESIGNAL", "HANDLER", "PROCEDURE", "FUNCTION",
    "TRIGGER", "VIEW", "INDEX", "SEQUENCE", "ALIAS", "SCHEMA",
    "AUTHORIZATION", "ROLE", "CONNECT", "DISCONNECT",
}
STRUCTURAL_PAREN_WORDS = {
    "FROM", "IN", "EXISTS", "VALUES", "OVER", "ON", "AS", "WHEN", "THEN",
    "ELSE", "NOT", "AND", "OR", "SETS", "USING",
}

OPERATION_FORBIDDEN = {
    "select": {"INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "REPLACE"},
    "insert": {"UPDATE", "DELETE", "MERGE", "CREATE", "REPLACE"},
    "update": {"INSERT", "DELETE", "MERGE", "CREATE", "REPLACE"},
    "create_table": {"INSERT", "UPDATE", "DELETE", "MERGE", "REPLACE"},
}


def tokenize(sql: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    length = len(sql)
    while i < length:
        ch = sql[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "-" and i + 1 < length and sql[i + 1] == "-":
            raise SQLPolicyError("SQL comments are not allowed")
        if ch == "/" and i + 1 < length and sql[i + 1] == "*":
            raise SQLPolicyError("SQL comments are not allowed")
        if ch == "'":
            start = i
            i += 1
            value: list[str] = []
            while i < length:
                if sql[i] == "'":
                    if i + 1 < length and sql[i + 1] == "'":
                        value.append("'")
                        i += 2
                        continue
                    i += 1
                    break
                value.append(sql[i])
                i += 1
            else:
                raise SQLPolicyError("Unterminated string literal")
            tokens.append(Token(TokenKind.STRING, "".join(value), start))
            continue
        if ch == '"':
            start = i
            i += 1
            value = []
            while i < length:
                if sql[i] == '"':
                    if i + 1 < length and sql[i + 1] == '"':
                        value.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                value.append(sql[i])
                i += 1
            else:
                raise SQLPolicyError("Unterminated quoted identifier")
            if not value:
                raise SQLPolicyError("Empty quoted identifier is not allowed")
            tokens.append(Token(TokenKind.QIDENT, "".join(value), start))
            continue
        if ch.isalpha() or ch in {"_", "$", "#", "@"}:
            start = i
            i += 1
            while i < length and (sql[i].isalnum() or sql[i] in {"_", "$", "#", "@"}):
                i += 1
            tokens.append(Token(TokenKind.WORD, sql[start:i], start))
            continue
        if ch.isdigit():
            start = i
            i += 1
            while i < length and (sql[i].isdigit() or sql[i] in {".", "e", "E", "+", "-"}):
                i += 1
            tokens.append(Token(TokenKind.NUMBER, sql[start:i], start))
            continue
        mapping = {
            ".": TokenKind.DOT, ",": TokenKind.COMMA, "(": TokenKind.LPAREN,
            ")": TokenKind.RPAREN, "?": TokenKind.PARAM, ";": TokenKind.SEMICOLON,
        }
        if ch in mapping:
            tokens.append(Token(mapping[ch], ch, i))
            i += 1
            continue
        if ch in "=<>!+-*/%|&:^":
            start = i
            i += 1
            while i < length and sql[i] in "=<>!+-*/%|&:^":
                i += 1
            tokens.append(Token(TokenKind.OP, sql[start:i], start))
            continue
        raise SQLPolicyError(f"Unsupported character at position {i}: {ch!r}")
    return tokens


def _strip_optional_trailing_semicolon(sql: str, tokens: list[Token]) -> tuple[str, list[Token]]:
    semicolons = [index for index, token in enumerate(tokens) if token.kind is TokenKind.SEMICOLON]
    if not semicolons:
        return sql.strip(), tokens
    if len(semicolons) != 1 or semicolons[0] != len(tokens) - 1:
        raise SQLPolicyError("Multiple SQL statements are not allowed")
    semicolon = tokens[-1]
    stripped = sql[: semicolon.position].rstrip()
    if not stripped:
        raise SQLPolicyError("SQL statement is empty")
    return stripped, tokens[:-1]


def _identifier(token: Token) -> str:
    if token.kind not in {TokenKind.WORD, TokenKind.QIDENT}:
        raise SQLPolicyError(f"Expected SQL identifier near position {token.position}")
    return token.value.upper()


def _qualified_name(tokens: list[Token], index: int) -> tuple[str, str, int]:
    if index >= len(tokens):
        raise SQLPolicyError("Expected a schema-qualified object name")
    schema = _identifier(tokens[index])
    if index + 2 >= len(tokens) or tokens[index + 1].kind is not TokenKind.DOT:
        raise SQLPolicyError("Unqualified table names are not allowed")
    table = _identifier(tokens[index + 2])
    if index + 3 < len(tokens) and tokens[index + 3].kind is TokenKind.DOT:
        raise SQLPolicyError("Three-part object names are not allowed")
    return schema, table, index + 3


def _word_tokens(tokens: Iterable[Token]) -> set[str]:
    return {token.upper for token in tokens if token.kind is TokenKind.WORD}


def _forbidden_keyword_check(tokens: list[Token], operation: str) -> None:
    words = _word_tokens(tokens)
    forbidden = (FORBIDDEN_ANYWHERE | OPERATION_FORBIDDEN[operation]) & words
    if forbidden:
        keyword = sorted(forbidden)[0]
        raise SQLPolicyError(f"Keyword {keyword} is not allowed for operation {operation}")


def _depths(tokens: list[Token]) -> list[int]:
    depths: list[int] = []
    depth = 0
    for token in tokens:
        depths.append(depth)
        if token.kind is TokenKind.LPAREN:
            depth += 1
        elif token.kind is TokenKind.RPAREN:
            depth -= 1
            if depth < 0:
                raise SQLPolicyError("Unbalanced parentheses")
    if depth != 0:
        raise SQLPolicyError("Unbalanced parentheses")
    return depths


def _extract_read_schemas(tokens: list[Token], depths: list[int]) -> tuple[str, ...]:
    schemas: list[str] = []
    for i, token in enumerate(tokens):
        if token.kind is not TokenKind.WORD or token.upper not in {"FROM", "JOIN"}:
            continue
        index = i + 1
        if index >= len(tokens):
            raise SQLPolicyError(f"Missing source after {token.upper}")
        if tokens[index].kind is TokenKind.LPAREN:
            continue
        if tokens[index].kind is TokenKind.WORD and tokens[index].upper in {
            "FINAL", "OLD", "NEW", "LATERAL", "TABLE", "UNNEST", "XMLTABLE", "JSON_TABLE"
        }:
            raise SQLPolicyError(f"Table construct {tokens[index].upper} is not allowed")
        schema, _table, _next = _qualified_name(tokens, index)
        if schema not in schemas:
            schemas.append(schema)

    for i, token in enumerate(tokens):
        if token.kind is not TokenKind.WORD or token.upper != "FROM":
            continue
        base_depth = depths[i]
        j = i + 1
        while j < len(tokens):
            current = tokens[j]
            depth = depths[j]
            if depth < base_depth:
                break
            if depth == base_depth and current.kind is TokenKind.WORD and current.upper in CLAUSE_END:
                break
            if depth == base_depth and current.kind is TokenKind.COMMA:
                source_index = j + 1
                if source_index >= len(tokens):
                    raise SQLPolicyError("Missing source after comma")
                if tokens[source_index].kind is TokenKind.LPAREN:
                    j += 1
                    continue
                schema, _table, _next = _qualified_name(tokens, source_index)
                if schema not in schemas:
                    schemas.append(schema)
            j += 1
    return tuple(schemas)


def _check_read_allowlist(read_schemas: tuple[str, ...], allowed: set[str]) -> None:
    denied = [schema for schema in read_schemas if schema not in allowed]
    if denied:
        raise SQLPolicyError("Read schema is not allowed: " + ", ".join(denied))



def _validate_routine_invocations(
    tokens: list[Token],
    allowed_functions: set[str],
    skip_token_indexes: set[int] | None = None,
) -> None:
    skip = skip_token_indexes or set()
    for index in range(len(tokens) - 1):
        token = tokens[index]
        if token.kind not in {TokenKind.WORD, TokenKind.QIDENT}:
            continue
        if tokens[index + 1].kind is not TokenKind.LPAREN:
            continue
        if index in skip or token.upper in STRUCTURAL_PAREN_WORDS:
            continue
        if (
            index >= 2
            and tokens[index - 1].kind is TokenKind.DOT
            and tokens[index - 2].kind in {TokenKind.WORD, TokenKind.QIDENT}
        ):
            schema = tokens[index - 2].upper
            raise SQLPolicyError(
                f"Schema-qualified routine invocation is not allowed: {schema}.{token.upper}"
            )
        if token.upper not in allowed_functions:
            raise SQLPolicyError(
                f"SQL function {token.upper} is not in SQL_ALLOWED_FUNCTIONS"
            )


def _validate_create_table_shape(tokens: list[Token], target_end: int) -> None:
    if target_end >= len(tokens) or tokens[target_end].kind is not TokenKind.LPAREN:
        raise SQLPolicyError("CREATE TABLE must use an explicit column definition list")
    depth = 0
    closing_index = None
    for index in range(target_end, len(tokens)):
        token = tokens[index]
        if token.kind is TokenKind.LPAREN:
            depth += 1
        elif token.kind is TokenKind.RPAREN:
            depth -= 1
            if depth == 0:
                closing_index = index
                break
            if depth < 0:
                raise SQLPolicyError("Unbalanced parentheses")
    if closing_index is None or closing_index != len(tokens) - 1:
        raise SQLPolicyError("CREATE TABLE options after the column list are not allowed")
    words = _word_tokens(tokens[target_end + 1 : closing_index])
    forbidden = {"SELECT", "FROM", "JOIN", "REFERENCES", "LIKE", "CALL"} & words
    if forbidden:
        raise SQLPolicyError("CREATE TABLE contains a forbidden construct: " + sorted(forbidden)[0])


def validate_sql(
    sql: str,
    declared_operation: str,
    allowed_read_schemas: Iterable[str],
    allowed_write_schema: str,
    allowed_functions: Iterable[str] = (),
    max_sql_length: int = 65535,
    max_parameters: int = 500,
    parameter_values_count: int | None = None,
) -> ValidatedSQL:
    operation = declared_operation.strip().lower().replace("-", "_")
    if operation not in {"select", "insert", "update", "create_table"}:
        raise SQLPolicyError(f"Unsupported declared operation: {declared_operation}")
    if "\x00" in sql:
        raise SQLPolicyError("NUL characters are not allowed")
    if len(sql) > max_sql_length:
        raise SQLPolicyError(f"SQL exceeds maximum length of {max_sql_length}")

    tokens = tokenize(sql)
    sql, tokens = _strip_optional_trailing_semicolon(sql, tokens)
    if not tokens:
        raise SQLPolicyError("SQL statement is empty")
    depths = _depths(tokens)

    expected_first = "CREATE" if operation == "create_table" else operation.upper()
    first = tokens[0]
    if first.kind is not TokenKind.WORD or first.upper != expected_first:
        actual = first.upper if first.kind in {TokenKind.WORD, TokenKind.QIDENT} else first.value
        raise SQLPolicyError(f"Declared operation {operation} does not match first SQL verb {actual}")

    _forbidden_keyword_check(tokens, operation)
    read_schemas: tuple[str, ...] = ()
    write_schema: str | None = None
    allowed_read = {value.strip().upper() for value in allowed_read_schemas if value.strip()}
    write_allowed = allowed_write_schema.strip().upper()
    allowed_function_set = {value.strip().upper() for value in allowed_functions if value.strip()}
    routine_skip_indexes: set[int] = set()

    if operation == "select":
        read_schemas = _extract_read_schemas(tokens, depths)
        _check_read_allowlist(read_schemas, allowed_read)
    elif operation == "insert":
        if len(tokens) < 4 or tokens[1].kind is not TokenKind.WORD or tokens[1].upper != "INTO":
            raise SQLPolicyError("INSERT must use INSERT INTO schema.table")
        write_schema, _table, target_end = _qualified_name(tokens, 2)
        routine_skip_indexes.add(target_end - 1)
        if write_schema != write_allowed:
            raise SQLPolicyError(f"Writes are allowed only in schema {write_allowed}")
        read_schemas = _extract_read_schemas(tokens, depths)
        _check_read_allowlist(read_schemas, allowed_read)
    elif operation == "update":
        write_schema, _table, target_end = _qualified_name(tokens, 1)
        if write_schema != write_allowed:
            raise SQLPolicyError(f"Writes are allowed only in schema {write_allowed}")
        if target_end >= len(tokens) or tokens[target_end].kind is not TokenKind.WORD or tokens[target_end].upper != "SET":
            raise SQLPolicyError("UPDATE must use UPDATE schema.table SET ...")
        read_schemas = _extract_read_schemas(tokens, depths)
        _check_read_allowlist(read_schemas, allowed_read)
    else:
        if len(tokens) < 5 or tokens[1].kind is not TokenKind.WORD or tokens[1].upper != "TABLE":
            raise SQLPolicyError("Only CREATE TABLE is allowed")
        write_schema, _table, target_end = _qualified_name(tokens, 2)
        if write_schema != write_allowed:
            raise SQLPolicyError(f"Tables may be created only in schema {write_allowed}")
        _validate_create_table_shape(tokens, target_end)

    if operation != "create_table":
        _validate_routine_invocations(tokens, allowed_function_set, routine_skip_indexes)

    parameter_count = sum(1 for token in tokens if token.kind is TokenKind.PARAM)
    if parameter_count > max_parameters:
        raise SQLPolicyError(f"SQL exceeds maximum parameter count of {max_parameters}")
    if parameter_values_count is not None and parameter_count != parameter_values_count:
        raise SQLPolicyError(
            f"Parameter count mismatch: SQL has {parameter_count} placeholders but request has {parameter_values_count} values"
        )

    return ValidatedSQL(
        sql=sql,
        operation=operation,
        read_schemas=read_schemas,
        write_schema=write_schema,
        parameter_count=parameter_count,
    )
