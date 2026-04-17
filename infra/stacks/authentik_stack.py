import json

from aws_cdk import (
    Aws,
    Duration,
    RemovalPolicy,
    Stack,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_rds as rds,
    aws_s3 as s3,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct

from ..constructs.database_instance import PrivateIsolatedDatabaseInstance
from ..constructs.fargate_service import PrivateEgressFargateService
from ..constructs.public_http_alb import PublicHttpAlb
from ..models.authentik_config import AuthentikConfig
from ..models.foundation_exports import FoundationExports
from ..models.instance_type import INSTANCE_TYPES


AUTHENTIK_HTTP_PORT = 9000
AUTHENTIK_DB_PORT = 5432
PRIVATE_CIDRS = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
]


class AuthentikStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        cfg: AuthentikConfig,
        shared: FoundationExports,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        ###
        # Secrets

        secret_key = secretsmanager.Secret(
            self,
            "SecretKey",
            secret_name="authentik/service/secret-key",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                password_length=50,
                exclude_punctuation=True,
                require_each_included_type=True,
            ),
        )
        bootstrap_password = secretsmanager.Secret(
            self,
            "BootstrapPassword",
            secret_name="authentik/bootstrap/password",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                password_length=32,
                require_each_included_type=True,
            ),
        )
        smtp_credentials = secretsmanager.Secret(
            self,
            "SmtpCredentials",
            secret_name="authentik/smtp-credentials",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                password_length=32,
                exclude_punctuation=True,
                require_each_included_type=True,
                generate_string_key="password",
                secret_string_template=json.dumps({"username": cfg.smtp.username}),
            ),
        )

        ###
        # Storage

        bucket = s3.Bucket(
            self,
            "Bucket",
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
            auto_delete_objects=False,
        )

        database = PrivateIsolatedDatabaseInstance(
            self,
            "Database",
            username=cfg.db.username,
            vpc=shared.vpc,
            instance_kwargs=dict(
                engine=rds.DatabaseInstanceEngine.postgres(
                    version=rds.PostgresEngineVersion.VER_16
                ),
                port=AUTHENTIK_DB_PORT,
                instance_type=INSTANCE_TYPES[cfg.db.instance_type],
                database_name=cfg.db.name,
                allocated_storage=cfg.db.allocated_storage_gib,
                max_allocated_storage=100,
                multi_az=False,
                backup_retention=Duration.days(7),
                deletion_protection=True,
                storage_encrypted=True,
                publicly_accessible=False,
            ),
        )

        ###
        # Services

        common_env = {
            "AUTHENTIK_DISABLE_UPDATE_CHECK": "true",
            "AUTHENTIK_EMAIL__FROM": cfg.smtp.from_email_address,
            "AUTHENTIK_EMAIL__HOST": cfg.smtp.host,
            "AUTHENTIK_EMAIL__PORT": str(cfg.smtp.port),
            "AUTHENTIK_EMAIL__USE_TLS": str(cfg.smtp.use_tls).lower(),
            "AUTHENTIK_EMAIL__USE_SSL": str(cfg.smtp.use_ssl).lower(),
            "AUTHENTIK_LISTEN__HTTP": f"0.0.0.0:{AUTHENTIK_HTTP_PORT}",
            "AUTHENTIK_POSTGRESQL__HOST": database.instance.db_instance_endpoint_address,
            "AUTHENTIK_POSTGRESQL__NAME": cfg.db.name,
            "AUTHENTIK_POSTGRESQL__PORT": str(AUTHENTIK_DB_PORT),
            "AUTHENTIK_STORAGE__BACKEND": "s3",
            "AUTHENTIK_STORAGE__S3__BUCKET_NAME": bucket.bucket_name,
            "AUTHENTIK_STORAGE__S3__REGION": Aws.REGION,
            "AUTHENTIK_LISTEN__TRUSTED_PROXY_CIDRS": ",".join(PRIVATE_CIDRS),
        }

        common_secrets = {
            "AUTHENTIK_EMAIL__PASSWORD": ecs.Secret.from_secrets_manager(smtp_credentials, "password"),
            "AUTHENTIK_EMAIL__USERNAME": ecs.Secret.from_secrets_manager(smtp_credentials, "username"),
            "AUTHENTIK_POSTGRESQL__PASSWORD": ecs.Secret.from_secrets_manager(database.secret, "password"),
            "AUTHENTIK_POSTGRESQL__USER": ecs.Secret.from_secrets_manager(database.secret, "username"),
            "AUTHENTIK_SECRET_KEY": ecs.Secret.from_secrets_manager(secret_key),
        }

        app_image = ecs.ContainerImage.from_registry(f"ghcr.io/goauthentik/server:{cfg.image_version}")
        health_check_path = "/-/health/live/"

        server_service = PrivateEgressFargateService(
            self,
            "ServerService",
            stream_prefix="authentik-server",
            cpu=cfg.server.cpu,
            memory_limit_mib=cfg.server.memory_limit_mib,
            desired_count=cfg.server.desired_count,
            vpc=shared.vpc,
            cluster=shared.cluster,
            container_kwargs=dict(
                image=app_image,
                port_mappings=[
                    ecs.PortMapping(container_port=AUTHENTIK_HTTP_PORT, host_port=AUTHENTIK_HTTP_PORT),
                ],
                command=["server"],
                environment={
                    **common_env,
                    "AUTHENTIK_BOOTSTRAP_EMAIL": cfg.bootstrap_email,
                },
                secrets={
                    **common_secrets,
                    "AUTHENTIK_BOOTSTRAP_PASSWORD": ecs.Secret.from_secrets_manager(bootstrap_password),
                },
                health_check=ecs.HealthCheck(
                    command=[
                        "CMD-SHELL",
                        f"wget -qO- http://127.0.0.1:{AUTHENTIK_HTTP_PORT}{health_check_path} >/dev/null || exit 1",
                    ],
                    interval=Duration.seconds(30),
                    timeout=Duration.seconds(5),
                    retries=3,
                    start_period=Duration.seconds(60),
                ),
            ),
        )
        worker_service = PrivateEgressFargateService(
            self,
            "WorkerService",
            stream_prefix="authentik-worker",
            cpu=cfg.worker.cpu,
            memory_limit_mib=cfg.worker.memory_limit_mib,
            desired_count=cfg.worker.desired_count,
            vpc=shared.vpc,
            cluster=shared.cluster,
            container_kwargs=dict(
                image=app_image,
                command=["worker"],
                environment=common_env,
                secrets=common_secrets,
            ),
        )

        alb = PublicHttpAlb(
            self,
            "PublicHttpAlb",
            fqdn=f"{cfg.subdomain}.{shared.domain}",
            a_record=cfg.subdomain,
            zone=shared.zone,
            vpc=shared.vpc,
        )

        alb.https_listener.add_targets(
            "AuthentikTargets",
            port=AUTHENTIK_HTTP_PORT,
            protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[server_service.service],
            health_check=elbv2.HealthCheck(
                path=health_check_path,
                healthy_http_codes="200",
            ),
        )

        ###
        # Permissions

        # Give server and worker services full access to S3 bucket.
        bucket_access_policy_statement = iam.PolicyStatement(
            actions=["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
            resources=[bucket.bucket_arn, f"{bucket.bucket_arn}/*"],
        )
        server_service.task_defn.add_to_task_role_policy(bucket_access_policy_statement)
        worker_service.task_defn.add_to_task_role_policy(bucket_access_policy_statement)

        # Allow server and worker services to connect to DB instance.
        database.instance.connections.allow_default_port_from(server_service.service)
        database.instance.connections.allow_default_port_from(worker_service.service)

        # Allow ALB to connect to server service HTTP port.
        server_service.security_group.add_ingress_rule(
            alb.security_group,
            ec2.Port.tcp(AUTHENTIK_HTTP_PORT),
            "ALB to Authentik HTTP",
        )
