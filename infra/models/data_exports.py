from dataclasses import dataclass

from aws_cdk import (
    aws_ec2 as ec2,
    aws_rds as rds,
    aws_secretsmanager as secretsmanager,
)


@dataclass(frozen=True)
class DataExports:
    instance: rds.IDatabaseInstance
    security_group: ec2.ISecurityGroup
    master_secret: secretsmanager.ISecret
