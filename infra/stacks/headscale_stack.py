from dataclasses import dataclass
from typing import cast

from aws_cdk import (
    CustomResource,
    Duration,
    Stack,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_lambda_python_alpha as lambda_python,
    aws_secretsmanager as secretsmanager,
    aws_servicediscovery as servicediscovery,
    custom_resources as cr,
)
from constructs import Construct

from ..constructs.fargate_service import PrivateEgressFargateService
from ..constructs.public_http_alb import PublicHttpAlb
from ..models.asset_loader import AssetLoader
from ..models.data_exports import DataExports
from ..models.foundation_exports import FoundationExports
from ..models.headscale_config import HeadscaleConfig

HEADSCALE_HTTP_PORT = 8080
HEADPLANE_HTTP_PORT = 3000
SERVICE_DISCOVERY_NAMESPACE = "headscale.local"
SERVICE_DISCOVERY_SERVICE = "headscale"
NOISE_VOLUME = "headscale-state"
NOISE_MOUNT_PATH = "/var/lib/headscale"
NOISE_KEY_FILENAME = "noise_private.key"


@dataclass(frozen=True)
class HeadscaleImports:
    cfg: HeadscaleConfig
    shared: FoundationExports
    data: DataExports
    assets: AssetLoader
    authentik_issuer_base: str


class HeadscaleStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        imports: HeadscaleImports,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cfg = imports.cfg
        shared = imports.shared
        data = imports.data
        assets = imports.assets
        authentik_issuer_base = imports.authentik_issuer_base

        headscale_fqdn = f"{cfg.headscale_subdomain}.{shared.public_domain}"
        headplane_fqdn = f"{cfg.headplane_subdomain}.{shared.public_domain}"
        base_domain = f"{cfg.dns_subdomain}.{shared.private_domain}"
        headscale_oidc_issuer = (
            f"{authentik_issuer_base}/{cfg.oidc_issuer_application}/"
        )
        headplane_oidc_issuer = f"{authentik_issuer_base}/{cfg.headplane_subdomain}/"

        ###
        # Secrets

        noise_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "NoiseKeySecret", "headscale/noise-private-key"
        )
        headscale_oidc_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "HeadscaleOidcSecret", "headscale/oidc"
        )
        admin_api_key_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "AdminApiKeySecret", "headscale/admin-api-key"
        )
        headplane_oidc_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "HeadplaneOidcSecret", "headplane/oidc"
        )
        headplane_cookie_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "HeadplaneCookieSecret", "headplane/cookie-secret"
        )
        db_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "DbSecret", cfg.db.secret_name
        )

        ###
        # Service discovery

        namespace = servicediscovery.PrivateDnsNamespace(
            self,
            "DnsNamespace",
            name=SERVICE_DISCOVERY_NAMESPACE,
            vpc=shared.vpc,
        )

        ###
        # Headscale service

        headscale_env = {
            "HEADSCALE_SERVER_URL": f"https://{headscale_fqdn}",
            "HEADSCALE_LISTEN_ADDR": f"0.0.0.0:{HEADSCALE_HTTP_PORT}",
            "HEADSCALE_METRICS_LISTEN_ADDR": "127.0.0.1:9090",
            "HEADSCALE_GRPC_LISTEN_ADDR": "127.0.0.1:50443",
            "HEADSCALE_DATABASE_TYPE": "postgres",
            "HEADSCALE_DATABASE_POSTGRES_HOST": data.database.instance.db_instance_endpoint_address,
            "HEADSCALE_DATABASE_POSTGRES_PORT": str(data.database.port),
            "HEADSCALE_DATABASE_POSTGRES_NAME": cfg.db.name,
            "HEADSCALE_DATABASE_POSTGRES_SSL": "true",
            "HEADSCALE_DNS_BASE_DOMAIN": base_domain,
            "HEADSCALE_DNS_MAGIC_DNS": "true",
            "HEADSCALE_DNS_NAMESERVERS_GLOBAL": ",".join(cfg.dns_nameservers),
            "HEADSCALE_DERP_URLS": "https://controlplane.tailscale.com/derpmap/default",
            "HEADSCALE_DERP_SERVER_ENABLED": "false",
            "HEADSCALE_OIDC_ISSUER": headscale_oidc_issuer,
            "HEADSCALE_OIDC_SCOPES": "openid,profile,email",
            "HEADSCALE_LOG_LEVEL": cfg.log_level,
            "HEADSCALE_DISABLE_CHECK_UPDATES": "true",
            "HEADSCALE_NOISE_PRIVATE_KEY_PATH": f"{NOISE_MOUNT_PATH}/{NOISE_KEY_FILENAME}",
        }

        headscale_secrets = {
            "HEADSCALE_DATABASE_POSTGRES_USER": ecs.Secret.from_secrets_manager(
                db_secret, "username"
            ),
            "HEADSCALE_DATABASE_POSTGRES_PASS": ecs.Secret.from_secrets_manager(
                db_secret, "password"
            ),
            "HEADSCALE_OIDC_CLIENT_ID": ecs.Secret.from_secrets_manager(
                headscale_oidc_secret, "client_id"
            ),
            "HEADSCALE_OIDC_CLIENT_SECRET": ecs.Secret.from_secrets_manager(
                headscale_oidc_secret, "client_secret"
            ),
        }

        headscale_service = PrivateEgressFargateService(
            self,
            "HeadscaleService",
            stream_prefix="headscale",
            cpu=cfg.headscale.cpu,
            memory_limit_mib=cfg.headscale.memory_limit_mib,
            desired_count=cfg.headscale.desired_count,
            min_healthy_percent=cfg.headscale.min_healthy_percent,
            vpc=shared.vpc,
            cluster=shared.cluster,
            container_kwargs=dict(
                image=ecs.ContainerImage.from_registry(
                    f"ghcr.io/juanfont/headscale:{cfg.headscale_image_version}"
                ),
                port_mappings=[
                    ecs.PortMapping(
                        container_port=HEADSCALE_HTTP_PORT,
                        host_port=HEADSCALE_HTTP_PORT,
                        name=SERVICE_DISCOVERY_SERVICE,
                    ),
                ],
                command=["serve"],
                environment=headscale_env,
                secrets=headscale_secrets,
            ),
        )

        headscale_service.task_defn.add_volume(name=NOISE_VOLUME)
        headscale_service.container.add_mount_points(
            ecs.MountPoint(
                container_path=NOISE_MOUNT_PATH,
                source_volume=NOISE_VOLUME,
                read_only=False,
            )
        )

        noise_init = headscale_service.task_defn.add_container(
            "NoiseKeyInit",
            image=ecs.ContainerImage.from_registry(
                "public.ecr.aws/aws-cli/aws-cli:latest"
            ),
            essential=False,
            entry_point=["sh", "-c"],
            command=[
                "; ".join(
                    [
                        "set -eu",
                        f'touch "{NOISE_MOUNT_PATH}/{NOISE_KEY_FILENAME}"',
                        f'chmod 600 "{NOISE_MOUNT_PATH}/{NOISE_KEY_FILENAME}"',
                        f'aws secretsmanager get-secret-value --secret-id "${{NOISE_SECRET_ARN}}" --query SecretString --output text | base64 -d > "{NOISE_MOUNT_PATH}/{NOISE_KEY_FILENAME}"',
                    ]
                )
            ],
            environment={"NOISE_SECRET_ARN": noise_secret.secret_arn},
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="headscale-noise-init",
                log_group=headscale_service.log_group,
            ),
        )
        noise_init.add_mount_points(
            ecs.MountPoint(
                container_path=NOISE_MOUNT_PATH,
                source_volume=NOISE_VOLUME,
                read_only=False,
            )
        )
        noise_secret.grant_read(headscale_service.task_defn.task_role)

        headscale_service.container.add_container_dependencies(
            ecs.ContainerDependency(
                container=noise_init,
                condition=ecs.ContainerDependencyCondition.SUCCESS,
            )
        )

        headscale_service.service.enable_cloud_map(
            cloud_map_namespace=namespace,
            name=SERVICE_DISCOVERY_SERVICE,
            dns_record_type=servicediscovery.DnsRecordType.A,
        )

        ###
        # Headplane service

        headplane_env = {
            "HEADPLANE_SERVER_HOST": "0.0.0.0",
            "HEADPLANE_SERVER_PORT": str(HEADPLANE_HTTP_PORT),
            "HEADPLANE_BASE_URL": f"https://{headplane_fqdn}",
            "HEADPLANE_HEADSCALE_URL": (
                f"http://{SERVICE_DISCOVERY_SERVICE}.{SERVICE_DISCOVERY_NAMESPACE}:{HEADSCALE_HTTP_PORT}"
            ),
            "HEADPLANE_OIDC_ISSUER": headplane_oidc_issuer,
        }

        headplane_secrets = {
            "HEADPLANE_HEADSCALE_API_KEY": ecs.Secret.from_secrets_manager(
                admin_api_key_secret
            ),
            "HEADPLANE_COOKIE_SECRET": ecs.Secret.from_secrets_manager(
                headplane_cookie_secret
            ),
            "HEADPLANE_OIDC_CLIENT_ID": ecs.Secret.from_secrets_manager(
                headplane_oidc_secret, "client_id"
            ),
            "HEADPLANE_OIDC_CLIENT_SECRET": ecs.Secret.from_secrets_manager(
                headplane_oidc_secret, "client_secret"
            ),
        }

        headplane_service = PrivateEgressFargateService(
            self,
            "HeadplaneService",
            stream_prefix="headplane",
            cpu=cfg.headplane.cpu,
            memory_limit_mib=cfg.headplane.memory_limit_mib,
            desired_count=cfg.headplane.desired_count,
            min_healthy_percent=cfg.headplane.min_healthy_percent,
            vpc=shared.vpc,
            cluster=shared.cluster,
            container_kwargs=dict(
                image=ecs.ContainerImage.from_registry(
                    f"ghcr.io/tale/headplane:{cfg.headplane_image_version}"
                ),
                port_mappings=[
                    ecs.PortMapping(
                        container_port=HEADPLANE_HTTP_PORT,
                        host_port=HEADPLANE_HTTP_PORT,
                    ),
                ],
                environment=headplane_env,
                secrets=headplane_secrets,
            ),
        )

        ###
        # ALB and routing

        alb = PublicHttpAlb(
            self,
            "PublicHttpAlb",
            fqdn=headscale_fqdn,
            a_record=cfg.headscale_subdomain,
            zone=shared.public_zone,
            vpc=shared.vpc,
            additional_fqdns=[headplane_fqdn],
        )

        alb.https_listener.add_targets(
            "HeadscaleTargets",
            priority=10,
            conditions=[elbv2.ListenerCondition.host_headers([headscale_fqdn])],
            port=HEADSCALE_HTTP_PORT,
            protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[headscale_service.service],
            deregistration_delay=Duration.seconds(30),
            health_check=elbv2.HealthCheck(
                path="/health",
                healthy_http_codes="200",
            ),
        )
        alb.https_listener.add_targets(
            "HeadplaneTargets",
            priority=20,
            conditions=[elbv2.ListenerCondition.host_headers([headplane_fqdn])],
            port=HEADPLANE_HTTP_PORT,
            protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[headplane_service.service],
            deregistration_delay=Duration.seconds(30),
            health_check=elbv2.HealthCheck(
                path="/",
                healthy_http_codes="200,302",
            ),
        )

        ###
        # Security group wiring

        headscale_service.security_group.add_ingress_rule(
            alb.security_group,
            ec2.Port.tcp(HEADSCALE_HTTP_PORT),
            "ALB to Headscale HTTP",
        )
        headscale_service.security_group.add_ingress_rule(
            headplane_service.security_group,
            ec2.Port.tcp(HEADSCALE_HTTP_PORT),
            "Headplane to Headscale (service discovery)",
        )
        headplane_service.security_group.add_ingress_rule(
            alb.security_group,
            ec2.Port.tcp(HEADPLANE_HTTP_PORT),
            "ALB to Headplane HTTP",
        )
        data.database.instance.connections.allow_default_port_from(
            headscale_service.service
        )

        ###
        # Admin API key custom resource

        api_key_fn = lambda_python.PythonFunction(
            self,
            "AdminApiKeyFn",
            entry=str(assets.lambda_path("headscale_admin_api_key")),
            runtime=lambda_.Runtime.PYTHON_3_12,
            index="index.py",
            handler="handler",
            timeout=Duration.minutes(10),
            environment={
                "CLUSTER_ARN": shared.cluster.cluster_arn,
                "TASK_DEFINITION_ARN": headscale_service.task_defn.task_definition_arn,
                "SUBNET_IDS": ",".join(s.subnet_id for s in shared.vpc.private_subnets),
                "SECURITY_GROUP_IDS": headscale_service.security_group.security_group_id,
                "SECRET_ID": admin_api_key_secret.secret_name,
                "CONTAINER_NAME": headscale_service.container.container_name,
                "LOG_GROUP": headscale_service.log_group.log_group_name,
                "LOG_STREAM_PREFIX": "headscale",
            },
        )
        api_key_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ecs:RunTask"],
                resources=[headscale_service.task_defn.task_definition_arn],
            )
        )
        api_key_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ecs:DescribeTasks", "logs:GetLogEvents"],
                resources=["*"],
            )
        )
        api_key_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[
                    headscale_service.task_defn.task_role.role_arn,
                    headscale_service.task_defn.obtain_execution_role().role_arn,
                ],
            )
        )
        admin_api_key_secret.grant_read(api_key_fn)
        admin_api_key_secret.grant_write(api_key_fn)

        provider = cr.Provider(
            self,
            "AdminApiKeyProvider",
            on_event_handler=cast(lambda_.IFunction, api_key_fn),
        )
        api_key_resource = CustomResource(
            self,
            "AdminApiKey",
            service_token=provider.service_token,
            properties={"Trigger": "v1"},
        )
        api_key_resource.node.add_dependency(headscale_service.service)
        headplane_service.node.add_dependency(api_key_resource)
