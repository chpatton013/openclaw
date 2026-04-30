import json
import os
import time
import urllib.error
import urllib.request

import boto3

sm = boto3.client("secretsmanager")

HEADSCALE_URL = os.environ["HEADSCALE_URL"]
ADMIN_KEY_SECRET = os.environ["ADMIN_KEY_SECRET"]
PREAUTHKEY_SECRET = os.environ["PREAUTHKEY_SECRET"]
PREAUTHKEY_USER = os.environ["PREAUTHKEY_USER"]
PLACEHOLDER = "pending"


def _get_admin_key() -> str:
    raw = sm.get_secret_value(SecretId=ADMIN_KEY_SECRET)["SecretString"]
    return json.loads(raw)["secret"]


def _current_preauthkey() -> str:
    try:
        raw = sm.get_secret_value(SecretId=PREAUTHKEY_SECRET).get("SecretString", "")
    except sm.exceptions.ResourceNotFoundException:
        return ""
    try:
        value = json.loads(raw).get("secret", "")
    except (json.JSONDecodeError, AttributeError):
        return ""
    return "" if value == PLACEHOLDER else value


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


def _ensure_user(key: str) -> str:
    """Create user if not exists; return the numeric user ID."""
    result = _api("POST", "user", key, {"name": PREAUTHKEY_USER})
    if "user" in result:
        return result["user"]["id"]
    # User already exists - list all and find by name.
    users = _api("GET", "user", key)
    for u in users.get("users", []):
        if u["name"] == PREAUTHKEY_USER:
            return u["id"]
    raise RuntimeError(f"Could not find or create user '{PREAUTHKEY_USER}': {result}")


def _create_preauthkey(key: str, user_id: str) -> str:
    expiry = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 365 * 86400))
    result = _api(
        "POST",
        "preauthkey",
        key,
        {
            "user": user_id,
            "reusable": True,
            "ephemeral": False,
            "expiration": expiry,
        },
    )
    preauthkey = result.get("preAuthKey", {}).get("key")
    if not preauthkey:
        raise RuntimeError(f"Failed to create preauthkey: {result}")
    return preauthkey


def handler(event, _ctx):
    request_type = event["RequestType"]
    if request_type == "Delete":
        return {
            "PhysicalResourceId": event.get(
                "PhysicalResourceId", "headscale-exit-node-preauthkey"
            )
        }

    if _current_preauthkey():
        return {"PhysicalResourceId": "headscale-exit-node-preauthkey"}

    key = _get_admin_key()
    user_id = _ensure_user(key)
    preauthkey = _create_preauthkey(key, user_id)

    sm.put_secret_value(
        SecretId=PREAUTHKEY_SECRET,
        SecretString=json.dumps({"secret": preauthkey}),
    )
    return {"PhysicalResourceId": "headscale-exit-node-preauthkey"}
