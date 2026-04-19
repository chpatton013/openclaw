from aws_cdk import (
    Duration,
    aws_apigatewayv2 as apigwv2,
    aws_apigatewayv2_integrations as apigwv2_integrations,
    aws_lambda as lambda_,
)
from constructs import Construct


class WebFingerApi(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        subject: str,
        oidc_issuer_url: str,
    ) -> None:
        super().__init__(scope, construct_id)

        handler = lambda_.Function(
            self,
            "Handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            timeout=Duration.seconds(5),
            code=lambda_.InlineCode(
                _render_handler(subject=subject, oidc_issuer_url=oidc_issuer_url)
            ),
        )

        self.api = apigwv2.HttpApi(self, "HttpApi")
        self.api.add_routes(
            path="/.well-known/webfinger",
            methods=[apigwv2.HttpMethod.GET],
            integration=apigwv2_integrations.HttpLambdaIntegration(
                "Integration", handler
            ),
        )


def _render_handler(*, subject: str, oidc_issuer_url: str) -> str:
    return f"""
import json

SUBJECT = {subject!r}
ISSUER = {oidc_issuer_url!r}

JRD_HEADERS = {{
    "content-type": "application/jrd+json",
    "cache-control": "public, max-age=300",
}}


def _response(status, body):
    return {{
        "statusCode": status,
        "headers": JRD_HEADERS,
        "body": json.dumps(body),
    }}


def handler(event, context):
    params = event.get("queryStringParameters") or {{}}
    resource = params.get("resource")
    if not resource:
        return _response(400, {{"error": "missing resource parameter"}})
    if resource != SUBJECT:
        return _response(404, {{"error": "unknown resource"}})
    return _response(200, {{
        "subject": SUBJECT,
        "links": [
            {{
                "rel": "http://openid.net/specs/connect/1.0/issuer",
                "href": ISSUER,
            }},
        ],
    }})
"""
