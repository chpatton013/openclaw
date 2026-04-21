from collections.abc import Sequence

from aws_cdk import (
    aws_ecs as ecs,
)
from constructs import Construct

from .fargate_service import PrivateEgressFargateService


class SharedVolumeInit(Construct):
    """An init container that seeds a volume shared with the main container.

    Adds a task-scoped volume, mounts it read-write on a non-essential
    `aws-cli` init container, mounts it on the main container, and wires a
    `SUCCESS` container dependency so the main container waits for the seed
    step.

    Caller is responsible for granting the task role any permissions the
    init command needs (e.g. `secret.grant_read(service.task_defn.task_role)`).
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        service: PrivateEgressFargateService,
        volume_name: str,
        mount_path: str,
        shell_commands: Sequence[str],
        environment: dict[str, str] | None = None,
        stream_prefix: str,
        main_container_read_only: bool = True,
    ) -> None:
        super().__init__(scope, construct_id)

        service.task_defn.add_volume(name=volume_name)
        service.container.add_mount_points(
            ecs.MountPoint(
                container_path=mount_path,
                source_volume=volume_name,
                read_only=main_container_read_only,
            )
        )

        self.container = service.task_defn.add_container(
            construct_id,
            image=ecs.ContainerImage.from_registry(
                "public.ecr.aws/aws-cli/aws-cli:latest"
            ),
            essential=False,
            entry_point=["sh", "-c"],
            command=["; ".join(["set -eu", *shell_commands])],
            environment=dict(environment) if environment else {},
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix=stream_prefix,
                log_group=service.log_group,
            ),
        )
        self.container.add_mount_points(
            ecs.MountPoint(
                container_path=mount_path,
                source_volume=volume_name,
                read_only=False,
            )
        )
        service.container.add_container_dependencies(
            ecs.ContainerDependency(
                container=self.container,
                condition=ecs.ContainerDependencyCondition.SUCCESS,
            )
        )
