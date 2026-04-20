import pathlib
from typing import cast

from aws_cdk import (
    CustomResource,
    Duration,
    Stack,
    aws_ec2 as ec2,
    aws_lambda as lambda_,
    aws_lambda_python_alpha as lambda_python,
    aws_rds as rds,
    aws_secretsmanager as secretsmanager,
    custom_resources as cr,
)
from constructs import Construct

from ..constructs.database_instance import PrivateIsolatedDatabaseInstance
from ..models.data_config import DataConfig
from ..models.data_exports import DataExports
from ..models.foundation_exports import FoundationExports
from ..models.instance_type import INSTANCE_TYPES

DB_PORT = 5432

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DB_INIT_ASSET = _REPO_ROOT / "scripts" / "cdk_assets" / "rds_logical_databases"


class DataStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        cfg: DataConfig,
        shared: FoundationExports,
        databases: list[str],
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.secret = secretsmanager.Secret.from_secret_name_v2(
            self, "DatabaseSecret", "data/database"
        )

        self.database = PrivateIsolatedDatabaseInstance(
            self,
            "Database",
            secret=self.secret,
            vpc=shared.vpc,
            instance_kwargs=dict(
                engine=rds.DatabaseInstanceEngine.postgres(
                    version=rds.PostgresEngineVersion.VER_16
                ),
                port=DB_PORT,
                instance_type=INSTANCE_TYPES[cfg.instance_type],
                allocated_storage=cfg.allocated_storage_gib,
                max_allocated_storage=100,
                multi_az=False,
                storage_encrypted=True,
                publicly_accessible=False,
            ),
        )

        init_fn = lambda_python.PythonFunction(
            self,
            "DbInitFn",
            entry=str(_DB_INIT_ASSET),
            runtime=lambda_.Runtime.PYTHON_3_12,
            index="index.py",
            handler="handler",
            timeout=Duration.minutes(2),
            vpc=shared.vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
        )
        self.secret.grant_read(init_fn)
        self.database.instance.connections.allow_default_port_from(init_fn)

        provider = cr.Provider(
            self,
            "DbInitProvider",
            on_event_handler=cast(lambda_.IFunction, init_fn),
        )
        init = CustomResource(
            self,
            "DbInit",
            service_token=provider.service_token,
            properties={
                "Host": self.database.instance.db_instance_endpoint_address,
                "Port": self.database.instance.db_instance_endpoint_port,
                "MasterSecretArn": self.secret.secret_arn,
                "Databases": databases,
            },
        )
        init.node.add_dependency(self.database.instance)

        self.exports = DataExports(
            instance=self.database.instance,
            security_group=self.database.security_group,
            master_secret=self.secret,
        )
