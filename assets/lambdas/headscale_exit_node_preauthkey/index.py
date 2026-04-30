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
NODE_HOSTNAME = os.environ["NODE_HOSTNAME"]
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


def _delete_stale_nodes(key: str) -> None:
    """Delete offline nodes for this hostname (canonical or
    collision-suffixed). Online nodes are left alone - this lambda runs
    before ECS replaces the task, so the currently-online registration
    still belongs to the live task."""
    result = _api("GET", "node", key)
    for node in result.get("nodes", []):
        given = node.get("givenName", "")
        if not (given == NODE_HOSTNAME or given.startswith(f"{NODE_HOSTNAME}-")):
            continue
        if node.get("online"):
            continue
        node_id = node["id"]
        print(f"Deleting stale offline node {node_id} ({given})")
        _api("DELETE", f"node/{node_id}", key)


def _stored_key_belongs_to_user(key: str, user_id: str, stored: str) -> bool:
    """Return True if `stored` is a current preauthkey for `user_id` in
    headscale. Guards against the orphaned-secret case: if the configured
    user is renamed (or deleted and recreated) while the secret still
    holds the old user's preauthkey, headscale rejects the registration
    with 'AuthKey not found'. Regenerate when this returns False."""
    result = _api("GET", f"preauthkey?user={user_id}", key)
    return any(k.get("key") == stored for k in result.get("preAuthKeys", []))


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

    key = _get_admin_key()
    user_id = _ensure_user(key)
    _delete_stale_nodes(key)

    stored = _current_preauthkey()
    if stored and _stored_key_belongs_to_user(key, user_id, stored):
        return {"PhysicalResourceId": "headscale-exit-node-preauthkey"}

    preauthkey = _create_preauthkey(key, user_id)

    sm.put_secret_value(
        SecretId=PREAUTHKEY_SECRET,
        SecretString=json.dumps({"secret": preauthkey}),
    )
    return {"PhysicalResourceId": "headscale-exit-node-preauthkey"}
