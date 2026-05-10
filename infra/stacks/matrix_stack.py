from dataclasses import dataclass

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_backup as backup,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_efs as efs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_events as events,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct

from ..constructs.fargate_service import PrivateEgressFargateService
from ..constructs.public_http_alb import PublicHttpAlb
from ..models.data_exports import DataExports
from ..models.foundation_exports import FoundationExports
from ..models.matrix_config import MatrixConfig

# Synapse listens on 8008 inside the container; ALB terminates TLS
# and forwards plain HTTP to this port. Both the client-server API
# and federation traffic share this single listener via the
# `client, federation` resources list - no separate 8448.
SYNAPSE_HTTP_PORT = 8008

# The Synapse Docker image runs as uid/gid 991. EFS access point
# enforces the same POSIX identity for files written by the task.
SYNAPSE_UID = "991"
SYNAPSE_GID = "991"

# Matrix `server_name` is the apex (e.g. chiiiirs.com), so MXIDs
# look like `@chris:chiiiirs.com`. The actual Synapse listener runs
# at matrix.<public_domain>; federating peers find it via the
# `.well-known/matrix/server` JSON served from the apex by SiteStack.


@dataclass(frozen=True)
class MatrixImports:
    cfg: MatrixConfig
    foundation: FoundationExports
    data: DataExports
    authentik_issuer_base: str


# Init script: rendered as the init container's command. Generates
# the Synapse signing key + one-time secrets on first boot, writes
# DB password and OIDC client secret from ECS-injected env vars onto
# EFS (so Synapse's homeserver.yaml can `_path`-reference them
# instead of inlining), and renders /data/homeserver.yaml from a
# heredoc template. Idempotent: re-runs leave existing keys/secrets
# untouched and only re-render the YAML.
_INIT_SCRIPT = r"""
set -euo pipefail

DATA=/data
SERVER_NAME="${SYNAPSE_SERVER_NAME}"
SIGNING_KEY="${DATA}/${SERVER_NAME}.signing.key"

# 1. Signing key (idempotent). Synapse needs this for federation
#    event signing and for E2E key cross-signing.
if [ ! -f "${SIGNING_KEY}" ]; then
  python -m synapse._scripts.generate_signing_key -o "${SIGNING_KEY}"
fi

# 2. One-time random secrets: macaroon (auth tokens), form (CSRF),
#    registration_shared_secret (used by the bot bootstrap CR in a
#    future phase to register the OpenClaw bot account).
for f in macaroon_secret_key form_secret registration_shared_secret; do
  if [ ! -f "${DATA}/${f}" ]; then
    head -c 32 /dev/urandom | base64 | tr -d '\n=' >"${DATA}/${f}"
    chmod 0600 "${DATA}/${f}"
  fi
done
MACAROON_KEY="$(cat "${DATA}/macaroon_secret_key")"
FORM_SECRET="$(cat "${DATA}/form_secret")"
REGISTRATION_SHARED_SECRET="$(cat "${DATA}/registration_shared_secret")"

# 3. Render homeserver.yaml. Bash interpolates ${...}; Synapse's
#    own template syntax `{{ user.preferred_username }}` passes
#    through as a literal string for Synapse to evaluate at OIDC
#    callback time. DB password and OIDC client_secret are inlined
#    from ECS-injected env vars - Synapse's database.args go
#    straight to psycopg2 which has no `password_path` knob, and
#    the YAML lives on an encrypted EFS access point restricted to
#    uid/gid 991 mode 750, so the exposure is equivalent to the
#    macaroon/form keys already in this file.
cat >"${DATA}/homeserver.yaml" <<EOF
server_name: "${SERVER_NAME}"
public_baseurl: "${PUBLIC_BASEURL}"
pid_file: /data/homeserver.pid

listeners:
  - port: ${SYNAPSE_PORT}
    type: http
    x_forwarded: true
    bind_addresses: ['0.0.0.0']
    resources:
      - names: [client, federation]
        compress: false

database:
  name: psycopg2
  args:
    user: ${DB_USER}
    password: "${DB_PASSWORD}"
    host: ${DB_HOST}
    port: ${DB_PORT}
    database: ${DB_NAME}
    sslmode: require
    cp_min: 5
    cp_max: 10

log_config: /data/log.config
media_store_path: /data/media_store
signing_key_path: ${SIGNING_KEY}

trusted_key_servers:
  - server_name: matrix.org

macaroon_secret_key: "${MACAROON_KEY}"
form_secret: "${FORM_SECRET}"
registration_shared_secret: "${REGISTRATION_SHARED_SECRET}"

enable_registration: false
enable_registration_without_verification: false
serve_server_wellknown: false
report_stats: false
suppress_key_server_warning: true

media_retention:
  remote_media_lifetime: ${REMOTE_MEDIA_LIFETIME}

oidc_providers:
  - idp_id: authentik
    idp_name: Authentik
    issuer: "${OIDC_ISSUER}"
    client_id: "${OIDC_CLIENT_ID}"
    client_secret: "${OIDC_CLIENT_SECRET}"
    scopes: [openid, profile, email]
    user_mapping_provider:
      config:
        localpart_template: "{{ user.preferred_username }}"
        display_name_template: "{{ user.name }}"
        email_template: "{{ user.email }}"
EOF

# 5. Minimal log config so Synapse logs to stdout (CloudWatch picks
#    it up via the awslogs driver).
cat >"${DATA}/log.config" <<'EOF'
version: 1
formatters:
  precise:
    format: '%(asctime)s - %(name)s - %(lineno)d - %(levelname)s - %(request)s - %(message)s'
handlers:
  console:
    class: logging.StreamHandler
    formatter: precise
loggers:
  synapse.storage.SQL:
    level: INFO
root:
  level: INFO
  handlers: [console]
disable_existing_loggers: false
EOF

echo "matrix-init: homeserver.yaml rendered for ${SERVER_NAME}"
"""


class MatrixStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        imports: MatrixImports,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cfg = imports.cfg
        foundation = imports.foundation
        data = imports.data

        # Matrix `server_name` is the apex; the listener lives at
        # matrix.<public_domain>. .well-known delegation in SiteStack
        # tells federating peers and clients to look here.
        server_name = foundation.public_domain
        listener_fqdn = f"{cfg.subdomain}.{foundation.public_domain}"
        oidc_issuer = f"{imports.authentik_issuer_base}/matrix/"

        ###
        # Secrets

        db_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "DbSecret", cfg.db.secret_name
        )
        oidc_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "OidcSecret", "authentik/oidc/matrix"
        )

        ###
        # EFS for /data (signing key, media store, log config,
        # registration shared secret, generated homeserver.yaml).

        efs_sg = ec2.SecurityGroup(
            self, "EfsSecurityGroup", vpc=foundation.vpc, allow_all_outbound=True
        )
        filesystem = efs.FileSystem(
            self,
            "MatrixFs",
            vpc=foundation.vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_group=efs_sg,
            encrypted=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_policy=efs.LifecyclePolicy.AFTER_14_DAYS,
            performance_mode=efs.PerformanceMode.GENERAL_PURPOSE,
            throughput_mode=efs.ThroughputMode.BURSTING,
        )
        access_point = filesystem.add_access_point(
            "DataAccessPoint",
            path="/synapse",
            create_acl=efs.Acl(
                owner_uid=SYNAPSE_UID, owner_gid=SYNAPSE_GID, permissions="750"
            ),
            posix_user=efs.PosixUser(uid=SYNAPSE_UID, gid=SYNAPSE_GID),
        )

        ###
        # Service: one Fargate task with init + main containers
        # sharing /data via EFS.

        common_environment = {
            "SYNAPSE_SERVER_NAME": server_name,
            "PUBLIC_BASEURL": f"https://{listener_fqdn}/",
            "SYNAPSE_PORT": str(SYNAPSE_HTTP_PORT),
            "DB_HOST": data.database.instance.db_instance_endpoint_address,
            "DB_PORT": str(data.database.port),
            "DB_NAME": cfg.db.name,
            "OIDC_ISSUER": oidc_issuer,
            "REMOTE_MEDIA_LIFETIME": cfg.remote_media_lifetime,
        }
        # Init container env additions only it needs: DB_USER plain,
        # DB_PASSWORD and OIDC_CLIENT_ID/SECRET as ECS secrets.
        init_environment = {
            **common_environment,
        }
        init_secrets = {
            "DB_USER": ecs.Secret.from_secrets_manager(db_secret, "username"),
            "DB_PASSWORD": ecs.Secret.from_secrets_manager(db_secret, "password"),
            "OIDC_CLIENT_ID": ecs.Secret.from_secrets_manager(oidc_secret, "client_id"),
            "OIDC_CLIENT_SECRET": ecs.Secret.from_secrets_manager(
                oidc_secret, "client_secret"
            ),
        }

        synapse_image = ecs.ContainerImage.from_registry(
            f"{foundation.dockerhub_mirror_base}/matrixdotorg/synapse:{cfg.image_version}"
        )

        service = PrivateEgressFargateService(
            self,
            "Service",
            stream_prefix="synapse",
            cpu=cfg.task.cpu,
            memory_limit_mib=cfg.task.memory_limit_mib,
            desired_count=cfg.task.desired_count,
            min_healthy_percent=cfg.task.min_healthy_percent,
            vpc=foundation.vpc,
            cluster=foundation.cluster,
            container_kwargs=dict(
                image=synapse_image,
                port_mappings=[
                    ecs.PortMapping(
                        container_port=SYNAPSE_HTTP_PORT,
                        host_port=SYNAPSE_HTTP_PORT,
                    ),
                ],
                # Main container reads /data/homeserver.yaml that the
                # init container rendered. Synapse's image
                # entrypoint (/start.py) honors SYNAPSE_CONFIG_PATH
                # and skips its own generate step when the file is
                # present.
                environment={
                    "SYNAPSE_CONFIG_PATH": "/data/homeserver.yaml",
                    "SYNAPSE_SERVER_NAME": server_name,
                    "SYNAPSE_REPORT_STATS": "no",
                },
            ),
            health_check_grace_period=Duration.seconds(120),
        )
        service.grant_pull_through_cache(foundation.dockerhub_mirror_namespace)

        # Init container: same image, overridden entrypoint runs the
        # bash script above. essential=False + SUCCESS dependency on
        # the main container means init runs once per task start,
        # exits 0, then Synapse starts.
        init_container = service.task_defn.add_container(
            "Init",
            image=synapse_image,
            essential=False,
            entry_point=["bash", "-c"],
            command=[_INIT_SCRIPT],
            environment=init_environment,
            secrets=init_secrets,
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="synapse-init",
                log_group=service.log_group,
            ),
        )

        ###
        # Shared /data EFS volume mounted by both containers.

        service.task_defn.add_volume(
            name="data",
            efs_volume_configuration=ecs.EfsVolumeConfiguration(
                file_system_id=filesystem.file_system_id,
                transit_encryption="ENABLED",
                authorization_config=ecs.AuthorizationConfig(
                    access_point_id=access_point.access_point_id,
                    iam="ENABLED",
                ),
            ),
        )
        service.container.add_mount_points(
            ecs.MountPoint(
                source_volume="data", container_path="/data", read_only=False
            )
        )
        init_container.add_mount_points(
            ecs.MountPoint(
                source_volume="data", container_path="/data", read_only=False
            )
        )
        service.container.add_container_dependencies(
            ecs.ContainerDependency(
                container=init_container,
                condition=ecs.ContainerDependencyCondition.SUCCESS,
            )
        )
        filesystem.grant_read_write(service.task_defn.task_role)
        efs_sg.add_ingress_rule(
            service.security_group,
            ec2.Port.tcp(2049),
            "Synapse task to EFS",
        )

        ###
        # ALB at matrix.<public_domain>. Terminates TLS, forwards
        # plain HTTP to Synapse on port 8008. Same listener serves
        # both Client-Server and federation traffic.

        alb = PublicHttpAlb(
            self,
            "PublicHttpAlb",
            fqdn=listener_fqdn,
            a_record=cfg.subdomain,
            zone=foundation.public_zone,
            vpc=foundation.vpc,
        )
        alb.https_listener.add_targets(
            "Targets",
            port=SYNAPSE_HTTP_PORT,
            protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[service.service],
            deregistration_delay=Duration.seconds(30),
            health_check=elbv2.HealthCheck(path="/health", healthy_http_codes="200"),
        )

        ###
        # SG + DB wiring

        service.security_group.add_ingress_rule(
            alb.security_group,
            ec2.Port.tcp(SYNAPSE_HTTP_PORT),
            "ALB to Synapse HTTP",
        )
        data.database.grant_connect(
            self,
            "MatrixDbIngress",
            peer=service.service,
            description="Synapse to DB",
        )

        ###
        # Backups (signing key + media + registration secret all
        # live on EFS; Postgres covered by RDS automated snapshots).

        backup_plan = backup.BackupPlan(
            self,
            "MatrixBackupPlan",
            backup_plan_name="matrix-efs-backups",
            backup_vault=foundation.backup_vault,
        )
        backup_plan.add_rule(
            backup.BackupPlanRule(
                rule_name="daily-10-days",
                schedule_expression=events.Schedule.cron(minute="0", hour="5"),
                delete_after=Duration.days(10),
            )
        )
        backup_plan.add_rule(
            backup.BackupPlanRule(
                rule_name="weekly-4-weeks",
                schedule_expression=events.Schedule.cron(
                    minute="0", hour="6", week_day="SUN"
                ),
                delete_after=Duration.days(28),
            )
        )
        backup_plan.add_rule(
            backup.BackupPlanRule(
                rule_name="monthly-3-months",
                schedule_expression=events.Schedule.cron(minute="0", hour="7", day="1"),
                delete_after=Duration.days(90),
            )
        )
        backup_plan.add_selection(
            "EfsSelection",
            resources=[backup.BackupResource.from_efs_file_system(filesystem)],
        )
