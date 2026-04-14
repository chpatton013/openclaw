from dataclasses import dataclass

from aws_cdk import (
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_route53 as route53,
)


@dataclass(frozen=True)
class FoundationExports:
    domain: str
    zone: route53.IHostedZone
    vpc: ec2.IVpc
    cluster: ecs.ICluster
    internal_sg: ec2.ISecurityGroup
