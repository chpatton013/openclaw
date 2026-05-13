"""ECR pull-through cache rule + its credential secret as a pair.

Wraps the two-step pattern Foundation uses to mirror an upstream
registry (GHCR, Docker Hub) into ECR: look up the credentials in
Secrets Manager, register the CfnPullThroughCacheRule, and expose
the computed `<account>.dkr.ecr.<region>.amazonaws.com/<namespace>`
URL prefix consumers use to pull through the mirror.
"""

from typing import Any

from aws_cdk import (
    Aws,
    aws_ecr as ecr,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct


class PullThroughCacheRule(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        secret_name: str,
        repository_prefix: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id)

        self.secret = secretsmanager.Secret.from_secret_name_v2(
            self, "CredentialSecret", secret_name
        )
        rule = ecr.CfnPullThroughCacheRule(
            self,
            "Rule",
            ecr_repository_prefix=repository_prefix,
            credential_arn=self.secret.secret_arn,
            **kwargs,
        )
        # ECR pull-through rules are keyed by `ecr_repository_prefix`
        # (a global account-region name), not by CFN logical id, so a
        # CFN logical-id change can't do the usual CREATE-then-DELETE
        # rename: the CREATE collides with the still-extant old rule.
        # Pin the underlying L1 resource's logical id to this
        # construct's own id (the pre-refactor name) so the CFN
        # template stays stable.
        rule.override_logical_id(construct_id)
        self.mirror_namespace = repository_prefix
        self.mirror_base = (
            f"{Aws.ACCOUNT_ID}.dkr.ecr.{Aws.REGION}.amazonaws.com/"
            f"{repository_prefix}"
        )
