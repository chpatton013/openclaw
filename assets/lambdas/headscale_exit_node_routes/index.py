import json
import os
import time
import urllib.error
import urllib.request

import boto3

ecs = boto3.client("ecs")
sm = boto3.client("secretsmanager")

HEADSCALE_URL = os.environ["HEADSCALE_URL"]
ADMIN_KEY_SECRET = os.environ["ADMIN_KEY_SECRET"]
NODE_HOSTNAME = os.environ.get("NODE_HOSTNAME", "aws-exit")
CLUSTER_ARN = os.environ["CLUSTER_ARN"]
TASK_DEFINITION_ARN = os.environ["TASK_DEFINITION_ARN"]
SUBNET_IDS = os.environ["SUBNET_IDS"].split(",")
SECURITY_GROUP_IDS = os.environ["SECURITY_GROUP_IDS"]
CONTAINER_NAME = os.environ["CONTAINER_NAME"]
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


def _find_node(key: str) -> tuple[str | None, list, list]:
    """Return (node_id, available_routes, approved_routes) for the online
    node matching NODE_HOSTNAME (or a collision-suffixed variant). Skips
    offline matches so a stale prior-deploy entry doesn't shadow the
    current task."""
    result = _api("GET", "node", key)
    for node in result.get("nodes", []):
        given = node.get("givenName", "")
        if not (given == NODE_HOSTNAME or given.startswith(f"{NODE_HOSTNAME}-")):
            continue
        if not node.get("online"):
            continue
        return (
            node["id"],
            node.get("availableRoutes", []),
            node.get("approvedRoutes", []),
        )
    return None, [], []


def _run_approve_task(node_id: str) -> str:
    response = ecs.run_task(
        cluster=CLUSTER_ARN,
        taskDefinition=TASK_DEFINITION_ARN,
        launchType="FARGATE",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": SUBNET_IDS,
                "securityGroups": [SECURITY_GROUP_IDS],
                "assignPublicIp": "DISABLED",
            }
        },
        overrides={
            "containerOverrides": [
                {
                    "name": CONTAINER_NAME,
                    "command": ["/usr/local/bin/approve-routes"],
                    "environment": [
                        {"name": "APPROVE_NODE_ID", "value": node_id},
                    ],
                }
            ]
        },
    )
    tasks = response.get("tasks") or []
    failures = response.get("failures") or []
    if failures or not tasks:
        raise RuntimeError(f"run_task failed: failures={failures}")
    return tasks[0]["taskArn"]


def _wait_for_stop(task_arn: str) -> dict:
    waiter = ecs.get_waiter("tasks_stopped")
    waiter.wait(
        cluster=CLUSTER_ARN,
        tasks=[task_arn],
        WaiterConfig={"Delay": 10, "MaxAttempts": 60},
    )
    described = ecs.describe_tasks(cluster=CLUSTER_ARN, tasks=[task_arn])
    return described["tasks"][0]


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
        node_id, available, approved = _find_node(key)
        print(
            f"Node {NODE_HOSTNAME!r}: id={node_id} available={available} approved={approved}"
        )
        if node_id and available:
            if set(available) <= set(approved):
                print(f"All routes already approved for {NODE_HOSTNAME}")
                return {"PhysicalResourceId": "headscale-exit-node-routes"}
            task_arn = _run_approve_task(node_id)
            print(f"Started approve-routes task {task_arn}")
            task = _wait_for_stop(task_arn)
            containers = task.get("containers") or []
            exit_code = next(
                (
                    c.get("exitCode")
                    for c in containers
                    if c.get("name") == CONTAINER_NAME
                ),
                None,
            )
            if exit_code != 0:
                raise RuntimeError(
                    f"approve-routes task exited with code {exit_code}: {task_arn}"
                )
            return {"PhysicalResourceId": "headscale-exit-node-routes"}
        time.sleep(15)

    raise RuntimeError(
        f"Node '{NODE_HOSTNAME}' not found or has no routes after {MAX_WAIT}s"
    )
