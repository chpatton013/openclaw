import json
from dataclasses import dataclass

from aws_cdk import (
    Duration,
    Stack,
    aws_backup as backup,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_efs as efs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct

from ..constructs.db_exec_tags import tag_for_db_exec
from ..constructs.fargate_service import PrivateEgressFargateService
from ..constructs.public_http_alb import PublicHttpAlb
from ..constructs.shared_efs_volume import EfsAccessPointSpec, SharedEfsVolume
from ..constructs.standard_backup_plan import StandardBackupPlan
from ..models.asset_loader import AssetLoader
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

# Matrix `server_name` is the apex (e.g. example.com), so MXIDs
# look like `@yourname:example.com`. The actual Synapse listener runs
# at matrix.<public_domain>; federating peers find it via the
# `.well-known/matrix/server` JSON served from the apex by ApexEdgeStack.


@dataclass(frozen=True)
class MatrixImports:
    cfg: MatrixConfig
    foundation: FoundationExports
    data: DataExports
    assets: AssetLoader
    authentik_issuer_base: str
    # Base URL of the self-hosted Element-Web client. Added to
    # Synapse's sso.client_whitelist so Element can be the
    # post-SSO redirect target (Synapse refuses redirects to
    # anything not on the list by default).
    element_web_base_url: str
    # Element-Call client base URL -- also a valid post-SSO
    # redirect target.
    element_call_base_url: str
    # TURN/coturn integration. Synapse hands clients ephemeral
    # HMAC-signed credentials computed from `turn_shared_secret`;
    # `turn_uris` is the list of `turn:` / `turns:` URIs the
    # client should try in order.
    turn_shared_secret: secretsmanager.ISecret
    turn_uris: list[str]
    turn_user_lifetime_seconds: int


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

        init_script = imports.assets.read_text("matrix", "init.sh")
        # Templates init.sh writes via os.path.expandvars at task
        # start. Kept as separate files in `assets/matrix/` so the
        # yamllint validator runs against them.
        homeserver_yaml_tmpl = imports.assets.read_text(
            "matrix", "homeserver.yaml.tmpl"
        )
        log_config_yaml = imports.assets.read_text("matrix", "log.config.yaml")

        # Matrix `server_name` is the apex; the listener lives at
        # matrix.<public_domain>. .well-known delegation in
        # ApexEdgeStack tells federating peers and clients to look here.
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

        efs_volume = SharedEfsVolume(
            self,
            "MatrixFs",
            vpc=foundation.vpc,
            access_points=[
                EfsAccessPointSpec(
                    id="DataAccessPoint",
                    path="/synapse",
                    create_acl=efs.Acl(
                        owner_uid=SYNAPSE_UID,
                        owner_gid=SYNAPSE_GID,
                        permissions="750",
                    ),
                    posix_user=efs.PosixUser(uid=SYNAPSE_UID, gid=SYNAPSE_GID),
                ),
            ],
            lifecycle_policy=efs.LifecyclePolicy.AFTER_14_DAYS,
        )
        efs_sg = efs_volume.security_group
        filesystem = efs_volume.filesystem
        access_point = efs_volume.access_points["DataAccessPoint"]

        ###
        # Service: one Fargate task with init + main containers
        # sharing /data via EFS.

        # Pre-render turn_uris as inline JSON; YAML accepts a JSON
        # array as a value, so the homeserver.yaml.tmpl stays valid
        # YAML pre-substitution. Synapse's turn_user_lifetime field
        # is milliseconds.
        turn_uris_json = json.dumps(imports.turn_uris)
        turn_user_lifetime_ms = str(imports.turn_user_lifetime_seconds * 1000)

        common_environment = {
            "SYNAPSE_SERVER_NAME": server_name,
            "PUBLIC_BASEURL": f"https://{listener_fqdn}/",
            "SYNAPSE_PORT": str(SYNAPSE_HTTP_PORT),
            "DB_HOST": data.database.instance.db_instance_endpoint_address,
            "DB_PORT": str(data.database.port),
            "DB_NAME": cfg.db.name,
            "OIDC_ISSUER": oidc_issuer,
            "REMOTE_MEDIA_LIFETIME": cfg.remote_media_lifetime,
            "ELEMENT_WEB_BASE_URL": imports.element_web_base_url,
            "ELEMENT_CALL_BASE_URL": imports.element_call_base_url,
            "TURN_URIS_JSON": turn_uris_json,
            "TURN_USER_LIFETIME_MS": turn_user_lifetime_ms,
        }
        # Init container env additions only it needs: DB_USER plain,
        # DB_PASSWORD and OIDC_CLIENT_ID/SECRET as ECS secrets. The
        # two `*_TMPL` / `*_YAML` env vars carry the homeserver.yaml +
        # log.config templates verbatim so init.sh can expand them
        # with python3 -- shipping the files this way (instead of
        # baking them into the image) keeps them in the repo where
        # yamllint can see them.
        init_environment = {
            **common_environment,
            "HOMESERVER_YAML_TMPL": homeserver_yaml_tmpl,
            "LOG_CONFIG_YAML": log_config_yaml,
        }
        init_secrets = {
            "DB_USER": ecs.Secret.from_secrets_manager(db_secret, "username"),
            "DB_PASSWORD": ecs.Secret.from_secrets_manager(db_secret, "password"),
            "OIDC_CLIENT_ID": ecs.Secret.from_secrets_manager(oidc_secret, "client_id"),
            "OIDC_CLIENT_SECRET": ecs.Secret.from_secrets_manager(
                oidc_secret, "client_secret"
            ),
            "TURN_SHARED_SECRET": ecs.Secret.from_secrets_manager(
                imports.turn_shared_secret, "secret"
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
                    # Same DB connection vars as the init container,
                    # for bin/db-sql: lets an ECS-Exec'd shell run
                    # python3 + psycopg2 (already bundled in the
                    # Synapse image) against the matrix DB without
                    # plumbing master credentials.
                    "DB_HOST": data.database.instance.db_instance_endpoint_address,
                    "DB_PORT": str(data.database.port),
                    "DB_NAME": cfg.db.name,
                },
                secrets={
                    "DB_USER": ecs.Secret.from_secrets_manager(db_secret, "username"),
                    "DB_PASSWORD": ecs.Secret.from_secrets_manager(
                        db_secret, "password"
                    ),
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
            command=[init_script],
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
        # Discoverable by bin/db-sql for ad-hoc SQL against the matrix
        # DB. Uses the matrix user (not master) -- sufficient for DML
        # on Synapse's own tables; the matrix user owns the schema.
        tag_for_db_exec(service.service, label="matrix")

        ###
        # Backups (signing key + media + registration secret all
        # live on EFS; Postgres covered by RDS automated snapshots).

        backup_plan = StandardBackupPlan(
            self,
            "MatrixBackupPlan",
            backup_plan_name="matrix-efs-backups",
            backup_vault=foundation.backup_vault,
        )
        backup_plan.backup_plan.add_selection(
            "EfsSelection",
            resources=[backup.BackupResource.from_efs_file_system(filesystem)],
        )
