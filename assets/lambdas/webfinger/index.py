import json
import os

SUBJECT = os.environ["WEBFINGER_SUBJECT"]
ISSUER = os.environ["WEBFINGER_ISSUER_URL"]

JRD_HEADERS = {
    "content-type": "application/jrd+json",
    "cache-control": "public, max-age=300",
}


def _response(status, body):
    return {
        "statusCode": status,
        "headers": JRD_HEADERS,
        "body": json.dumps(body),
    }


def handler(event, context):
    params = event.get("queryStringParameters") or {}
    resource = params.get("resource")
    if not resource:
        return _response(400, {"error": "missing resource parameter"})
    if resource != SUBJECT:
        return _response(404, {"error": "unknown resource"})
    return _response(
        200,
        {
            "subject": SUBJECT,
            "links": [
                {
                    "rel": "http://openid.net/specs/connect/1.0/issuer",
                    "href": ISSUER,
                },
            ],
        },
    )
