import json
import re

import boto3
import pg8000.native

sm = boto3.client("secretsmanager")

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"invalid identifier: {name!r}")
    return name


def _get_secret(arn: str) -> dict:
    return json.loads(sm.get_secret_value(SecretId=arn)["SecretString"])


def _connect(host: str, port: int, user: str, password: str, database: str):
    return pg8000.native.Connection(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        ssl_context=True,
    )


def _escape_literal(value: str) -> str:
    return value.replace("'", "''")


def handler(event, _ctx):
    request_type = event["RequestType"]
    if request_type == "Delete":
        return {
            "PhysicalResourceId": event.get(
                "PhysicalResourceId", "rds-logical-databases"
            )
        }

    props = event["ResourceProperties"]
    host = props["Host"]
    port = int(props["Port"])
    master = _get_secret(props["MasterSecretArn"])
    master_user = master["username"]
    master_password = master["password"]

    admin = _connect(host, port, master_user, master_password, "postgres")
    try:
        existing_dbs = {
            row[0] for row in (admin.run("SELECT datname FROM pg_database") or [])
        }
        existing_roles = {
            row[0] for row in (admin.run("SELECT rolname FROM pg_roles") or [])
        }
        provisioned = []
        for entry in props["Databases"]:
            db = _validate_ident(entry["Name"])
            user = _validate_ident(entry["User"])
            user_secret = _get_secret(entry["SecretArn"])
            secret_username = user_secret.get("username")
            if secret_username != user:
                raise ValueError(
                    f"secret {entry['SecretArn']!r} username "
                    f"{secret_username!r} does not match {user!r}"
                )
            password_literal = _escape_literal(user_secret["password"])

            if user in existing_roles:
                admin.run(
                    f"ALTER ROLE \"{user}\" WITH LOGIN PASSWORD '{password_literal}'"
                )
            else:
                admin.run(
                    f"CREATE ROLE \"{user}\" WITH LOGIN PASSWORD '{password_literal}'"
                )
                existing_roles.add(user)

            admin.run(f'GRANT "{user}" TO CURRENT_USER')

            if db in existing_dbs:
                admin.run(f'ALTER DATABASE "{db}" OWNER TO "{user}"')
            else:
                admin.run(f'CREATE DATABASE "{db}" OWNER "{user}"')
                existing_dbs.add(db)

            provisioned.append((db, user))
    finally:
        admin.close()

    for db, user in provisioned:
        db_conn = _connect(host, port, master_user, master_password, db)
        try:
            db_conn.run(f'REASSIGN OWNED BY "{master_user}" TO "{user}"')
            db_conn.run(f'ALTER SCHEMA public OWNER TO "{user}"')
        finally:
            db_conn.close()

    return {"PhysicalResourceId": "rds-logical-databases"}
