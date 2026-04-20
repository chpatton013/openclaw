from typing import cast

from aws_cdk import (
    Stack,
    aws_apigatewayv2 as apigwv2,
    aws_certificatemanager as acm,
    aws_route53 as route53,
    aws_route53_targets as route53_targets,
)
from constructs import Construct

from ..constructs.webfinger_api import WebFingerApi
from ..models.foundation_exports import FoundationExports
from ..models.webfinger_config import WebFingerConfig


class WebFingerStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        cfg: WebFingerConfig,
        shared: FoundationExports,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        webfinger = WebFingerApi(
            self,
            "Api",
            subject=cfg.subject,
            oidc_issuer_url=cfg.oidc_issuer_url,
        )

        certificate = acm.Certificate(
            self,
            "Certificate",
            domain_name=shared.public_domain,
            validation=acm.CertificateValidation.from_dns(shared.public_zone),
        )

        domain = apigwv2.DomainName(
            self,
            "DomainName",
            domain_name=shared.public_domain,
            certificate=certificate,
        )

        apigwv2.ApiMapping(
            self,
            "ApiMapping",
            api=webfinger.api,
            domain_name=domain,
            stage=webfinger.api.default_stage,
        )

        route53.ARecord(
            self,
            "AliasRecord",
            zone=shared.public_zone,
            record_name=shared.public_domain,
            target=route53.RecordTarget.from_alias(
                cast(
                    route53.IAliasRecordTarget,
                    route53_targets.ApiGatewayv2DomainProperties(
                        domain.regional_domain_name,
                        domain.regional_hosted_zone_id,
                    ),
                )
            ),
        )
