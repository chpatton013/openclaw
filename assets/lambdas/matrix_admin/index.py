"""Matrix DB admin SQL runner.

Runs parameterized SQL against the matrix Postgres database as the
master user. Intended for one-off admin ops: recovering a bot's
access token, clearing stale e2e_* rows, deactivating ghost user
records. Invoked via `aws lambda invoke`.

Event shape:
  {
    "queries": [
      {"sql": "...", "params": {"name": value, ...}},
      ...
    ],
    "commit": false  // omit/false for SELECTs; set true to persist DML
  }
Returns:
  {"results": [{"rows": [...], "columns": [...], "row_count": N}, ...]}
"""

import json
import os
from datetime import date, datetime

import boto3
import pg8000.native

sm = boto3.client("secretsmanager")


def _connect() -> pg8000.native.Connection:
    master = json.loads(
        sm.get_secret_value(SecretId=os.environ["MASTER_SECRET_ARN"])["SecretString"]
    )
    return pg8000.native.Connection(
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        user=master["username"],
        password=master["password"],
        database=os.environ["DB_NAME"],
        ssl_context=True,
    )


def _coerce(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("latin-1", errors="replace")
    return value


def handler(event, _ctx):
    queries = event.get("queries", [])
    if not isinstance(queries, list) or not queries:
        raise ValueError("event must include non-empty 'queries' list")
    commit = bool(event.get("commit", False))

    conn = _connect()
    results: list[dict] = []
    try:
        if commit:
            conn.run("BEGIN")
        try:
            for q in queries:
                sql = q["sql"]
                params = q.get("params") or {}
                rows = conn.run(sql, **params) or []
                columns = [c["name"] for c in (conn.columns or [])]
                results.append(
                    {
                        "rows": [[_coerce(v) for v in row] for row in rows],
                        "columns": columns,
                        "row_count": len(rows),
                    }
                )
            if commit:
                conn.run("COMMIT")
        except Exception:
            if commit:
                try:
                    conn.run("ROLLBACK")
                except Exception:
                    pass
            raise
    finally:
        conn.close()
    return {"results": results}
