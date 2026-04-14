from dataclasses import dataclass

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_certificatemanager as acm,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_rds as rds,
    aws_route53 as route53,
    aws_route53_targets as route53_targets,
    aws_s3 as s3,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct

from ..models.app_config import AppConfig
from ..models.foundation_exports import FoundationExports


class FoundationStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, cfg: AppConfig, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        zone = route53.HostedZone.from_lookup(
            self,
            "HostedZone",
            domain_name=cfg.root_domain,
        )

        vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="private-egress",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="private-isolated",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
        )

        cluster = ecs.Cluster(self, "Cluster", vpc=vpc)

        internal_sg = ec2.SecurityGroup(
            self,
            "InternalServicesSecurityGroup",
            vpc=vpc,
            allow_all_outbound=True,
        )

        self.exports = FoundationExports(
            domain=cfg.root_domain,
            zone=zone,
            vpc=vpc,
            cluster=cluster,
            internal_sg=internal_sg,
        )

