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
    aws_logs as logs,
    aws_secretsmanager as secretsmanager,
    aws_servicediscovery as servicediscovery,
    custom_resources as cr,
)
from constructs import Construct

from aws_cdk import aws_ecr_assets as ecr_assets

from ..constructs.fargate_service import PrivateEgressFargateService
from ..constructs.public_http_alb import PublicHttpAlb
from ..constructs.shared_volume_init import SharedVolumeInit
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
CONFIG_FILENAME = "config.yaml"

HEADPLANE_CONFIG_VOLUME = "headplane-config"
HEADPLANE_CONFIG_MOUNT_PATH = "/etc/headplane"


@dataclass(frozen=True)
class HeadscaleImports:
    cfg: HeadscaleConfig
    foundation: FoundationExports
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
        foundation = imports.foundation
        data = imports.data
        assets = imports.assets
        authentik_issuer_base = imports.authentik_issuer_base

        headscale_fqdn = f"{cfg.headscale_subdomain}.{foundation.public_domain}"
        base_domain = f"{cfg.dns_subdomain}.{foundation.private_domain}"
        headscale_oidc_issuer = (
            f"{authentik_issuer_base}/{cfg.oidc_issuer_application}/"
        )
        # Headplane is served at headscale_fqdn/admin; "headplane" is the
        # Authentik application slug (matches the blueprint), not a subdomain.
        headplane_oidc_issuer = f"{authentik_issuer_base}/headplane/"

        ###
        # Secrets

        noise_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "NoiseKeySecret", "headscale/noise-private-key"
        )
        headscale_oidc_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "HeadscaleOidcSecret", "authentik/oidc/headscale"
        )
        admin_api_key_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "AdminApiKeySecret", "headscale/admin-api-key"
        )
        headplane_oidc_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "HeadplaneOidcSecret", "authentik/oidc/headplane"
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
            vpc=foundation.vpc,
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
            "HEADSCALE_PREFIXES_V4": "100.64.0.0/10",
            "HEADSCALE_PREFIXES_V6": "fd7a:115c:a1e0::/48",
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
            vpc=foundation.vpc,
            cluster=foundation.cluster,
            container_kwargs=dict(
                image=ecs.ContainerImage.from_registry(
                    f"{foundation.ghcr_mirror_base}/juanfont/headscale"
                    f":{cfg.headscale_image_version}"
                ),
                port_mappings=[
                    ecs.PortMapping(
                        container_port=HEADSCALE_HTTP_PORT,
                        host_port=HEADSCALE_HTTP_PORT,
                        name=SERVICE_DISCOVERY_SERVICE,
                    ),
                ],
                command=[
                    "serve",
                    "--config",
                    f"{NOISE_MOUNT_PATH}/{CONFIG_FILENAME}",
                ],
                environment=headscale_env,
                secrets=headscale_secrets,
            ),
        )

        SharedVolumeInit(
            self,
            "NoiseKeyInit",
            service=headscale_service,
            volume_name=NOISE_VOLUME,
            mount_path=NOISE_MOUNT_PATH,
            shell_commands=[
                f'touch "{NOISE_MOUNT_PATH}/{NOISE_KEY_FILENAME}"',
                f'chmod 600 "{NOISE_MOUNT_PATH}/{NOISE_KEY_FILENAME}"',
                " | ".join(
                    [
                        f'aws secretsmanager get-secret-value --secret-id "${{NOISE_SECRET_NAME}}" --query SecretString --output text',
                        "jq -r .secret",
                        "base64 -d",
                        "od -An -v -t x1",
                        'tr -d "[:space:]"',
                        f'awk \'{{print "privkey:" $0}}\' >"{NOISE_MOUNT_PATH}/{NOISE_KEY_FILENAME}"',
                    ]
                ),
                f'printf "noise:\\n  private_key_path: {NOISE_MOUNT_PATH}/{NOISE_KEY_FILENAME}\\n" >"{NOISE_MOUNT_PATH}/{CONFIG_FILENAME}"',
            ],
            environment={"NOISE_SECRET_NAME": "headscale/noise-private-key"},
            stream_prefix="headscale-noise-init",
            main_container_read_only=False,
        )
        noise_secret.grant_read(headscale_service.task_defn.task_role)

        headscale_service.grant_pull_through_cache(foundation.ghcr_mirror_namespace)

        headscale_service.service.enable_cloud_map(
            cloud_map_namespace=namespace,
            name=SERVICE_DISCOVERY_SERVICE,
            dns_record_type=servicediscovery.DnsRecordType.A,
        )

        ###
        # Headplane service

        headplane_service = PrivateEgressFargateService(
            self,
            "HeadplaneService",
            stream_prefix="headplane",
            cpu=cfg.headplane.cpu,
            memory_limit_mib=cfg.headplane.memory_limit_mib,
            desired_count=cfg.headplane.desired_count,
            min_healthy_percent=cfg.headplane.min_healthy_percent,
            vpc=foundation.vpc,
            cluster=foundation.cluster,
            container_kwargs=dict(
                image=ecs.ContainerImage.from_registry(
                    f"{foundation.ghcr_mirror_base}/tale/headplane"
                    f":{cfg.headplane_image_version}"
                ),
                port_mappings=[
                    ecs.PortMapping(
                        container_port=HEADPLANE_HTTP_PORT,
                        host_port=HEADPLANE_HTTP_PORT,
                    ),
                ],
                # /admin/healthz returns 200 OK or 500 ERROR. The production
                # image is distroless so curl is absent; use the bundled node.
                health_check=ecs.HealthCheck(
                    command=[
                        "CMD",
                        "/nodejs/bin/node",
                        "-e",
                        f"fetch('http://localhost:{HEADPLANE_HTTP_PORT}/admin/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))",
                    ],
                    interval=Duration.seconds(30),
                    timeout=Duration.seconds(10),
                    retries=3,
                    start_period=Duration.seconds(30),
                ),
            ),
        )

        headplane_service.grant_pull_through_cache(foundation.ghcr_mirror_namespace)

        # headplane v0.6+ requires a config file at /etc/headplane/config.yaml.
        # The init container fetches secrets via AWS CLI + jq, then writes the
        # config file using a Python heredoc (avoids shell quoting issues).
        _headscale_url = (
            f"http://{SERVICE_DISCOVERY_SERVICE}"
            f".{SERVICE_DISCOVERY_NAMESPACE}:{HEADSCALE_HTTP_PORT}"
        )
        # Fetch steps use ${{VAR}} (CDK escaping for literal ${VAR} in shell).
        # Secrets are JSON {"secret": "..."} so .secret is extracted via jq.
        _fetch_cookie = f'COOKIE="$(aws secretsmanager get-secret-value --secret-id "${{HP_COOKIE_NAME}}" --query SecretString --output text | jq -r .secret)"'
        _fetch_apikey = f'APIKEY="$(aws secretsmanager get-secret-value --secret-id "${{HP_APIKEY_NAME}}" --query SecretString --output text | jq -r .secret)"'
        _fetch_oidc = f'OIDC="$(aws secretsmanager get-secret-value --secret-id "${{HP_OIDC_NAME}}" --query SecretString --output text)"'
        _parse_oidc = 'CLIENT_ID="$(echo "$OIDC" | jq -r .client_id)"'
        _parse_secret = 'CLIENT_SECRET="$(echo "$OIDC" | jq -r .client_secret)"'
        _export = "export COOKIE APIKEY CLIENT_ID CLIENT_SECRET"
        # Python heredoc: 'PYEOF' prevents shell expansion inside Python code.
        _pyeof = "\n".join(
            [
                "python3 << 'PYEOF'",
                "import json, os",
                "cfg = {",
                "    'server': {'host': '0.0.0.0', 'port': 3000,",
                "               'cookie_secret': os.environ['COOKIE'], 'cookie_secure': False, 'data_path': '/tmp/headplane/'},",
                "    'headscale': {'url': os.environ['HP_HEADSCALE_URL'], 'config_strict': False},",
                "    'oidc': {",
                "        'issuer': os.environ['HP_OIDC_ISSUER'],",
                "        'client_id': os.environ['CLIENT_ID'],",
                "        'client_secret': os.environ['CLIENT_SECRET'],",
                "        'token_endpoint_auth_method': 'client_secret_basic',",
                "        'redirect_uri': os.environ['HP_REDIRECT_URI'],",
                "        'disable_api_key_login': False,",
                "        'headscale_api_key': os.environ['APIKEY'],",
                "    },",
                "}",
                "open('/etc/headplane/config.yaml', 'w').write(json.dumps(cfg, indent=2))",
                "print('headplane config.yaml written')",
                "PYEOF",
            ]
        )
        SharedVolumeInit(
            self,
            "HeadplaneConfigInit",
            service=headplane_service,
            volume_name=HEADPLANE_CONFIG_VOLUME,
            mount_path=HEADPLANE_CONFIG_MOUNT_PATH,
            shell_commands=[
                _fetch_cookie,
                _fetch_apikey,
                _fetch_oidc,
                _parse_oidc,
                _parse_secret,
                _export,
                _pyeof,
            ],
            environment={
                "HP_COOKIE_NAME": "headplane/cookie-secret",
                "HP_APIKEY_NAME": "headscale/admin-api-key",
                "HP_OIDC_NAME": "authentik/oidc/headplane",
                "HP_HEADSCALE_URL": _headscale_url,
                "HP_OIDC_ISSUER": headplane_oidc_issuer,
                "HP_REDIRECT_URI": f"https://{headscale_fqdn}/admin/oidc/callback",
            },
            stream_prefix="headplane-config-init",
        )
        # Grant the task role (used by init container shell scripts) read access.
        # Using grant_read() generates name-?????? ARN patterns that match the
        # full ARN resolved when secrets are fetched by name.
        headplane_cookie_secret.grant_read(headplane_service.task_defn.task_role)
        headplane_oidc_secret.grant_read(headplane_service.task_defn.task_role)
        admin_api_key_secret.grant_read(headplane_service.task_defn.task_role)

        ###
        # ALB and routing

        alb = PublicHttpAlb(
            self,
            "PublicHttpAlb",
            fqdn=headscale_fqdn,
            a_record=cfg.headscale_subdomain,
            zone=foundation.public_zone,
            vpc=foundation.vpc,
        )

        # Headplane is served at /admin on the headscale subdomain.
        # Route /admin and /admin/* to headplane before the headscale catch-all.
        alb.https_listener.add_targets(
            "HeadplaneTargets",
            priority=5,
            conditions=[
                elbv2.ListenerCondition.host_headers([headscale_fqdn]),
                elbv2.ListenerCondition.path_patterns(["/admin", "/admin/*"]),
            ],
            port=HEADPLANE_HTTP_PORT,
            protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[headplane_service.service],
            deregistration_delay=Duration.seconds(30),
            health_check=elbv2.HealthCheck(
                path="/admin/healthz",
                healthy_http_codes="200",
            ),
        )
        alb.https_listener.add_targets(
            "HeadscaleTargets",
            priority=25,
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
        alb.https_listener.add_action(
            "MisdirectedRequest",
            action=elbv2.ListenerAction.fixed_response(
                421,
                content_type="text/plain",
                message_body="Misdirected request",
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
        data.database.grant_connect(
            self,
            "HeadscaleDbIngress",
            peer=headscale_service.service,
            description="Headscale to DB",
        )

        ###
        # Admin API key custom resource

        # Dedicated task definition for the one-off API key creation task.
        # headscale 0.26+ apikeys create is a gRPC client requiring a running
        # server, so we start headscale serve in the background, wait for gRPC
        # to be ready, then run apikeys create against 127.0.0.1:50443.
        api_key_task_defn = ecs.FargateTaskDefinition(
            self,
            "ApiKeyTaskDefn",
            cpu=cfg.headscale.cpu,
            memory_limit_mib=cfg.headscale.memory_limit_mib,
            task_role=headscale_service.task_defn.task_role,
            execution_role=headscale_service.task_defn.obtain_execution_role(),
        )
        api_key_task_defn.add_volume(name=NOISE_VOLUME)
        api_key_log_group = logs.LogGroup(self, "ApiKeyLogGroup")
        # Custom image bundles the headscale binary on Alpine (distroless
        # production image has no shell) with the create-api-key entrypoint.
        api_key_image = ecs.ContainerImage.from_docker_image_asset(
            ecr_assets.DockerImageAsset(
                self,
                "ApiKeyImage",
                directory=str(assets.docker_path("headscale_api_key")),
                platform=ecr_assets.Platform.LINUX_AMD64,
            )
        )
        api_key_container = api_key_task_defn.add_container(
            "Container",
            image=api_key_image,
            environment=headscale_env,
            secrets=headscale_secrets,
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="headscale",
                log_group=api_key_log_group,
            ),
        )
        api_key_container.add_mount_points(
            ecs.MountPoint(
                container_path=NOISE_MOUNT_PATH,
                source_volume=NOISE_VOLUME,
                read_only=False,
            )
        )
        # Init container writes the noise key and minimal config to the volume.
        _noise_init_cmds = [
            f'touch "{NOISE_MOUNT_PATH}/{NOISE_KEY_FILENAME}"',
            f'chmod 600 "{NOISE_MOUNT_PATH}/{NOISE_KEY_FILENAME}"',
            " | ".join(
                [
                    f'aws secretsmanager get-secret-value --secret-id "${{NOISE_SECRET_NAME}}" --query SecretString --output text',
                    "jq -r .secret",
                    "base64 -d",
                    "od -An -v -t x1",
                    'tr -d "[:space:]"',
                    f'awk \'{{print "privkey:" $0}}\' >"{NOISE_MOUNT_PATH}/{NOISE_KEY_FILENAME}"',
                ]
            ),
            f'printf "noise:\\n  private_key_path: {NOISE_MOUNT_PATH}/{NOISE_KEY_FILENAME}\\n" >"{NOISE_MOUNT_PATH}/{CONFIG_FILENAME}"',
        ]
        api_key_init = api_key_task_defn.add_container(
            "NoiseKeyInit",
            image=ecs.ContainerImage.from_registry(
                "public.ecr.aws/aws-cli/aws-cli:latest"
            ),
            essential=False,
            entry_point=["sh", "-c"],
            command=["; ".join(["set -eu", *_noise_init_cmds])],
            environment={"NOISE_SECRET_NAME": "headscale/noise-private-key"},
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="headscale-noise-init",
                log_group=api_key_log_group,
            ),
        )
        api_key_init.add_mount_points(
            ecs.MountPoint(
                container_path=NOISE_MOUNT_PATH,
                source_volume=NOISE_VOLUME,
                read_only=False,
            )
        )
        api_key_container.add_container_dependencies(
            ecs.ContainerDependency(
                container=api_key_init,
                condition=ecs.ContainerDependencyCondition.SUCCESS,
            )
        )
        # Grant the task's role permission to read the noise secret.
        noise_secret.grant_read(api_key_task_defn.task_role)
        # Grant ECR pull-through cache access for the headscale image.
        stack = Stack.of(self)
        execution_role = api_key_task_defn.obtain_execution_role()
        execution_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["ecr:GetAuthorizationToken"],
                resources=["*"],
            )
        )
        execution_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=[
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchGetImage",
                    "ecr:CreateRepository",
                    "ecr:BatchImportUpstreamImage",
                ],
                resources=[
                    f"arn:aws:ecr:{stack.region}:{stack.account}:repository/{foundation.ghcr_mirror_namespace}/*"
                ],
            )
        )

        api_key_fn = lambda_python.PythonFunction(
            self,
            "AdminApiKeyFn",
            entry=str(assets.lambda_path("headscale_admin_api_key")),
            runtime=lambda_.Runtime.PYTHON_3_12,
            index="index.py",
            handler="handler",
            timeout=Duration.minutes(10),
            environment={
                "CLUSTER_ARN": foundation.cluster.cluster_arn,
                "TASK_DEFINITION_ARN": api_key_task_defn.task_definition_arn,
                "SUBNET_IDS": ",".join(
                    s.subnet_id for s in foundation.vpc.private_subnets
                ),
                "SECURITY_GROUP_IDS": headscale_service.security_group.security_group_id,
                "SECRET_ID": admin_api_key_secret.secret_name,
                "CONTAINER_NAME": api_key_container.container_name,
                "LOG_GROUP": api_key_log_group.log_group_name,
                "LOG_STREAM_PREFIX": "headscale",
            },
        )
        api_key_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ecs:RunTask"],
                resources=[api_key_task_defn.task_definition_arn],
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
                    api_key_task_defn.task_role.role_arn,
                    api_key_task_defn.obtain_execution_role().role_arn,
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
