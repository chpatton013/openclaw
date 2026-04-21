from typing import cast

from aws_cdk import (
    aws_apigatewayv2 as apigwv2,
    aws_certificatemanager as acm,
    aws_route53 as route53,
    aws_route53_targets as route53_targets,
)
from constructs import Construct


class PublicHttpApi(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        fqdn: str,
        a_record: str,
        zone: route53.IHostedZone,
    ) -> None:
        super().__init__(scope, construct_id)

        self.api = apigwv2.HttpApi(self, "HttpApi")

        self.certificate = acm.Certificate(
            self,
            "Certificate",
            domain_name=fqdn,
            validation=acm.CertificateValidation.from_dns(zone),
        )

        self.domain = apigwv2.DomainName(
            self,
            "DomainName",
            domain_name=fqdn,
            certificate=self.certificate,
        )

        apigwv2.ApiMapping(
            self,
            "ApiMapping",
            api=self.api,
            domain_name=self.domain,
            stage=self.api.default_stage,
        )

        self.a_record = route53.ARecord(
            self,
            "AliasRecord",
            zone=zone,
            record_name=a_record,
            target=route53.RecordTarget.from_alias(
                cast(
                    route53.IAliasRecordTarget,
                    route53_targets.ApiGatewayv2DomainProperties(
                        self.domain.regional_domain_name,
                        self.domain.regional_hosted_zone_id,
                    ),
                )
            ),
        )
