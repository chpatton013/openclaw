import json
import re

import boto3
import pg8000.native

sm = boto3.client("secretsmanager")

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"invalid database identifier: {name!r}")
    return name


def handler(event, _ctx):
    request_type = event["RequestType"]
    if request_type == "Delete":
        return {
            "PhysicalResourceId": event.get(
                "PhysicalResourceId", "rds-logical-databases"
            )
        }

    props = event["ResourceProperties"]
    secret = json.loads(
        sm.get_secret_value(SecretId=props["MasterSecretArn"])["SecretString"]
    )
    conn = pg8000.native.Connection(
        host=props["Host"],
        port=int(props["Port"]),
        user=secret["username"],
        password=secret["password"],
        database="postgres",
        ssl_context=True,
    )
    try:
        existing = {
            row[0] for row in (conn.run("SELECT datname FROM pg_database") or [])
        }
        for raw_name in props["Databases"]:
            name = _validate_ident(raw_name)
            if name not in existing:
                conn.run(f'CREATE DATABASE "{name}"')
    finally:
        conn.close()

    return {"PhysicalResourceId": "rds-logical-databases"}
