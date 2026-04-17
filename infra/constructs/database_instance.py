import json

from typing import Any

from aws_cdk import (
    aws_ec2 as ec2,
    aws_rds as rds,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct


class PrivateIsolatedDatabaseInstance(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        username: str,
        vpc: ec2.IVpc,
        instance_kwargs: dict[str, Any],
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.secret = secretsmanager.Secret(
            self,
            "Secret",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                password_length=32,
                exclude_punctuation=True,
                require_each_included_type=True,
                generate_string_key="password",
                secret_string_template=json.dumps({"username": username}),
            ),
        )

        self.security_group = ec2.SecurityGroup(
            self, "SecurityGroup", vpc=vpc, allow_all_outbound=True
        )

        self.instance = rds.DatabaseInstance(
            self,
            "Instance",
            credentials=rds.Credentials.from_secret(self.secret),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            security_groups=[self.security_group],
            **instance_kwargs
        )
