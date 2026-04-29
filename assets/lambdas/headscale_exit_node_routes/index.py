import json
import os
import time
import urllib.error
import urllib.request

import boto3

sm = boto3.client("secretsmanager")

HEADSCALE_URL = os.environ["HEADSCALE_URL"]
ADMIN_KEY_SECRET = os.environ["ADMIN_KEY_SECRET"]
NODE_HOSTNAME = os.environ.get("NODE_HOSTNAME", "aws-exit")
MAX_WAIT = int(os.environ.get("MAX_WAIT_SECONDS", "300"))


def _get_admin_key() -> str:
    raw = sm.get_secret_value(SecretId=ADMIN_KEY_SECRET)["SecretString"]
    return json.loads(raw)["secret"]


def _api(method: str, path: str, key: str, body=None):
    url = f"{HEADSCALE_URL}/api/v1/{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def _find_node_id(key: str) -> str | None:
    result = _api("GET", "node", key)
    for node in result.get("nodes", []):
        given = node.get("givenName", "")
        name = node.get("name", "")
        if given == NODE_HOSTNAME or name.startswith(NODE_HOSTNAME):
            return node["id"]
    return None


def _approve_routes(key: str, node_id: str) -> list[str]:
    result = _api("GET", f"node/{node_id}/routes", key)
    approved = []
    for route in result.get("routes", []):
        if not route.get("enabled"):
            _api("POST", f"routes/{route['id']}/enable", key)
            approved.append(route["prefix"])
    return approved


def handler(event, _ctx):
    request_type = event["RequestType"]
    if request_type == "Delete":
        return {
            "PhysicalResourceId": event.get(
                "PhysicalResourceId", "headscale-exit-node-routes"
            )
        }

    key = _get_admin_key()
    deadline = time.time() + MAX_WAIT
    while time.time() < deadline:
        node_id = _find_node_id(key)
        if node_id:
            routes = _api("GET", f"node/{node_id}/routes", key).get("routes", [])
            if routes:
                approved = _approve_routes(key, node_id)
                print(
                    f"Approved routes for {NODE_HOSTNAME} (node {node_id}): {approved or 'already enabled'}"
                )
                return {"PhysicalResourceId": "headscale-exit-node-routes"}
        time.sleep(15)

    raise RuntimeError(
        f"Node '{NODE_HOSTNAME}' not found or has no routes after {MAX_WAIT}s"
    )
