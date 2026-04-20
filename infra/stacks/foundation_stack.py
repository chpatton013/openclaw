from dataclasses import dataclass

from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_route53 as route53,
)
from constructs import Construct

from ..models.foundation_config import FoundationConfig
from ..models.foundation_exports import FoundationExports


@dataclass(frozen=True)
class FoundationImports:
    cfg: FoundationConfig


class FoundationStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        imports: FoundationImports,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cfg = imports.cfg

        public_zone = route53.HostedZone.from_lookup(
            self,
            "PublicZone",
            domain_name=cfg.public_domain,
        )
        private_zone = route53.HostedZone.from_lookup(
            self,
            "PrivateZone",
            domain_name=cfg.private_domain,
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

        self.exports = FoundationExports(
            public_domain=cfg.public_domain,
            public_zone=public_zone,
            private_domain=cfg.private_domain,
            private_zone=private_zone,
            vpc=vpc,
            cluster=cluster,
        )
