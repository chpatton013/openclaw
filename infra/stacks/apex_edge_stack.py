from dataclasses import dataclass
from typing import cast

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_certificatemanager as acm,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_route53 as route53,
    aws_route53_targets as route53_targets,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
)
from constructs import Construct

from ..models.apex_edge_config import ApexEdgeConfig
from ..models.asset_loader import AssetLoader
from ..models.foundation_exports import FoundationExports


@dataclass(frozen=True)
class ApexBehavior:
    """One additional CloudFront behavior on the apex distribution.

    Path pattern is matched against the request path; matching
    requests are routed to the behavior's origin instead of the
    default S3 origin. Caller supplies a fully-formed BehaviorOptions
    so it can set cache policies, origin request policies, methods,
    etc. without this construct having an opinion.
    """

    path_pattern: str
    options: cloudfront.BehaviorOptions


@dataclass(frozen=True)
class ApexContentDeployment:
    """A static-content deployment onto the apex S3 bucket.

    Each entry produces one `s3deploy.BucketDeployment` resource.
    Mirrors the BucketDeployment kwargs (sources, content_type,
    distribution_paths, prune). The `construct_id` is the CDK id used
    when the deployment is added to the stack.
    """

    construct_id: str
    sources: list[s3deploy.ISource]
    content_type: str | None = None
    distribution_paths: list[str] | None = None
    prune: bool = True


@dataclass(frozen=True)
class ApexEdgeImports:
    cfg: ApexEdgeConfig
    foundation: FoundationExports
    assets: AssetLoader
    # CloudFront behaviors contributed by other stacks. Each one
    # routes a path pattern on the apex hostname to a non-default
    # origin (e.g. WebFinger's API Gateway).
    behaviors: list[ApexBehavior]
    # Static content deployed into the apex S3 bucket by other
    # stacks (e.g. Matrix's `.well-known/matrix/{server,client}`
    # discovery JSON).
    content_deployments: list[ApexContentDeployment]


class ApexEdgeStack(Stack):
    """The apex CloudFront distribution + ACM cert + origin S3 bucket.

    All apex routing lives here, but the knowledge of WHAT is being
    routed lives in `app_builder.py`, which assembles the `behaviors`
    and `content_deployments` lists from each contributing stack's
    exports. This stack does not know about WebFinger, Matrix, or any
    other specific service by name.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        imports: ApexEdgeImports,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        cfg = imports.cfg
        foundation = imports.foundation
        apex_fqdn = foundation.public_domain
        names = [apex_fqdn]
        if cfg.www_subdomain:
            names.append(f"{cfg.www_subdomain}.{apex_fqdn}")

        # Private S3 bucket; CloudFront reads via Origin Access Control.
        bucket = s3.Bucket(
            self,
            "ApexBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        cert = acm.Certificate(
            self,
            "Certificate",
            domain_name=apex_fqdn,
            subject_alternative_names=names[1:],
            validation=acm.CertificateValidation.from_dns(foundation.public_zone),
        )

        distribution = cloudfront.Distribution(
            self,
            "Cdn",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            ),
            additional_behaviors={b.path_pattern: b.options for b in imports.behaviors},
            domain_names=names,
            certificate=cert,
            default_root_object="index.html",
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=404,
                    response_page_path="/index.html",
                    ttl=Duration.seconds(60),
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=404,
                    response_page_path="/index.html",
                    ttl=Duration.seconds(60),
                ),
            ],
        )

        # Default site content (the apex's static landing page).
        s3deploy.BucketDeployment(
            self,
            "SiteContent",
            sources=[s3deploy.Source.asset(str(imports.assets.site_path()))],
            destination_bucket=bucket,
            distribution=distribution,
            distribution_paths=["/*"],
        )

        # Per-stack content contributions.
        for entry in imports.content_deployments:
            kwargs_extra: dict = {}
            if entry.content_type is not None:
                kwargs_extra["content_type"] = entry.content_type
            if entry.distribution_paths is not None:
                kwargs_extra["distribution_paths"] = entry.distribution_paths
            s3deploy.BucketDeployment(
                self,
                entry.construct_id,
                sources=entry.sources,
                destination_bucket=bucket,
                distribution=distribution,
                prune=entry.prune,
                **kwargs_extra,
            )

        target = route53.RecordTarget.from_alias(
            cast(
                route53.IAliasRecordTarget,
                route53_targets.CloudFrontTarget(distribution),
            )
        )
        route53.ARecord(
            self,
            "ApexA",
            zone=foundation.public_zone,
            record_name=apex_fqdn,
            target=target,
        )
        if cfg.www_subdomain:
            route53.ARecord(
                self,
                "WwwA",
                zone=foundation.public_zone,
                record_name=f"{cfg.www_subdomain}.{apex_fqdn}",
                target=target,
            )
