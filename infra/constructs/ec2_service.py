from collections.abc import Sequence
from typing import Any

from aws_cdk import (
    Stack,
    aws_autoscaling as autoscaling,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_iam as iam,
    aws_logs as logs,
)
from constructs import Construct


class PrivateEgressEc2Service(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        stream_prefix: str,
        vpc: ec2.IVpc,
        instance_type: str,
        desired_count: int = 1,
        min_capacity: int = 1,
        max_capacity: int = 1,
        network_mode: ecs.NetworkMode = ecs.NetworkMode.HOST,
        user_data_commands: Sequence[str] = (),
        container_kwargs: dict[str, Any],
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.asg = autoscaling.AutoScalingGroup(
            self,
            "Asg",
            vpc=vpc,
            instance_type=ec2.InstanceType(instance_type),
            machine_image=ecs.EcsOptimizedImage.amazon_linux2023(),
            min_capacity=min_capacity,
            max_capacity=max_capacity,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
        )
        self.asg.role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "AmazonSSMManagedInstanceCore"
            )
        )
        self.asg.role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AmazonEC2ContainerServiceforEC2Role"
            )
        )
        if user_data_commands:
            self.asg.user_data.add_commands(*user_data_commands)

        self.cluster = ecs.Cluster(self, "Cluster", vpc=vpc)
        self.capacity_provider = ecs.AsgCapacityProvider(
            self,
            "CapacityProvider",
            auto_scaling_group=self.asg,
            enable_managed_termination_protection=False,
        )
        self.cluster.add_asg_capacity_provider(self.capacity_provider)

        self.log_group = logs.LogGroup(self, "LogGroup")
        self.task_defn = ecs.Ec2TaskDefinition(
            self,
            "TaskDefn",
            network_mode=network_mode,
        )
        self.container = self.task_defn.add_container(
            "Container",
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix=stream_prefix, log_group=self.log_group
            ),
            **container_kwargs,
        )
        self.service = ecs.Ec2Service(
            self,
            "Service",
            cluster=self.cluster,
            task_definition=self.task_defn,
            desired_count=desired_count,
            capacity_provider_strategies=[
                ecs.CapacityProviderStrategy(
                    capacity_provider=self.capacity_provider.capacity_provider_name,
                    weight=1,
                )
            ],
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=False),
            enable_execute_command=True,
        )
        self.task_defn.task_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=[
                    "ssmmessages:CreateControlChannel",
                    "ssmmessages:CreateDataChannel",
                    "ssmmessages:OpenControlChannel",
                    "ssmmessages:OpenDataChannel",
                ],
                resources=["*"],
            )
        )

        self.security_group = self.asg.connections.security_groups[0]

    def grant_pull_through_cache(self, namespace: str) -> None:
        stack = Stack.of(self)
        execution_role = self.task_defn.obtain_execution_role()
        repo_arn = (
            f"arn:aws:ecr:{stack.region}:{stack.account}:repository/{namespace}/*"
        )
        auth_stmt = iam.PolicyStatement(
            actions=["ecr:GetAuthorizationToken"],
            resources=["*"],
        )
        pull_stmt = iam.PolicyStatement(
            actions=[
                "ecr:BatchCheckLayerAvailability",
                "ecr:GetDownloadUrlForLayer",
                "ecr:BatchGetImage",
                "ecr:CreateRepository",
                "ecr:BatchImportUpstreamImage",
            ],
            resources=[repo_arn],
        )
        for role in (execution_role, self.asg.role):
            role.add_to_principal_policy(auth_stmt)
            role.add_to_principal_policy(pull_stmt)
