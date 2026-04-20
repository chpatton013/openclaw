from dataclasses import dataclass

from aws_cdk import aws_secretsmanager as secretsmanager

from ..constructs.database_instance import PrivateIsolatedDatabaseInstance


@dataclass(frozen=True)
class DataExports:
    database: PrivateIsolatedDatabaseInstance
    master_secret: secretsmanager.ISecret
