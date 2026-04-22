from dataclasses import dataclass
from typing import cast

from aws_cdk import (
    Duration,
    Stack,
    aws_apigatewayv2 as apigwv2,
    aws_apigatewayv2_integrations as apigwv2_integrations,
    aws_lambda as lambda_,
)
from constructs import Construct

from ..constructs.public_http_api import PublicHttpApi
from ..models.asset_loader import AssetLoader
from ..models.foundation_exports import FoundationExports
from ..models.webfinger_config import WebFingerConfig


@dataclass(frozen=True)
class WebFingerImports:
    cfg: WebFingerConfig
    foundation: FoundationExports
    assets: AssetLoader
    authentik_issuer_base: str


class WebFingerStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        imports: WebFingerImports,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cfg = imports.cfg
        foundation = imports.foundation
        oidc_issuer_url = (
            f"{imports.authentik_issuer_base}/{cfg.oidc_issuer_application}/"
        )

        handler = lambda_.Function(
            self,
            "Handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            timeout=Duration.seconds(5),
            code=lambda_.Code.from_asset(str(imports.assets.lambda_path("webfinger"))),
            environment={
                "WEBFINGER_SUBJECT": cfg.subject,
                "WEBFINGER_ISSUER_URL": oidc_issuer_url,
            },
        )

        public_api = PublicHttpApi(
            self,
            "Api",
            fqdn=foundation.public_domain,
            a_record=foundation.public_domain,
            zone=foundation.public_zone,
        )
        public_api.api.add_routes(
            path="/.well-known/webfinger",
            methods=[apigwv2.HttpMethod.GET],
            integration=apigwv2_integrations.HttpLambdaIntegration(
                "Integration", cast(lambda_.IFunction, handler)
            ),
        )
