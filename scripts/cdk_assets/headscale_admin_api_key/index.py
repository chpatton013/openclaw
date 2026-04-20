import json
import os
import time

import boto3

ecs = boto3.client("ecs")
sm = boto3.client("secretsmanager")
logs = boto3.client("logs")

CLUSTER_ARN = os.environ["CLUSTER_ARN"]
TASK_DEFINITION_ARN = os.environ["TASK_DEFINITION_ARN"]
SUBNET_IDS = os.environ["SUBNET_IDS"].split(",")
SECURITY_GROUP_IDS = os.environ["SECURITY_GROUP_IDS"].split(",")
SECRET_ID = os.environ["SECRET_ID"]
CONTAINER_NAME = os.environ["CONTAINER_NAME"]


def _current_secret() -> str:
    try:
        return sm.get_secret_value(SecretId=SECRET_ID).get("SecretString", "")
    except sm.exceptions.ResourceNotFoundException:
        return ""


def _run_task() -> str:
    response = ecs.run_task(
        cluster=CLUSTER_ARN,
        taskDefinition=TASK_DEFINITION_ARN,
        launchType="FARGATE",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": SUBNET_IDS,
                "securityGroups": SECURITY_GROUP_IDS,
                "assignPublicIp": "DISABLED",
            }
        },
        overrides={
            "containerOverrides": [
                {
                    "name": CONTAINER_NAME,
                    "command": [
                        "headscale",
                        "apikeys",
                        "create",
                        "--expiration",
                        "0",
                        "--output",
                        "json",
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


def _fetch_log_output(task_arn: str, stream_prefix: str, log_group: str) -> str:
    task_id = task_arn.rsplit("/", 1)[-1]
    stream_name = f"{stream_prefix}/{CONTAINER_NAME}/{task_id}"
    for _ in range(12):
        try:
            events = logs.get_log_events(
                logGroupName=log_group, logStreamName=stream_name, startFromHead=True
            )
            if events["events"]:
                return "\n".join(e["message"] for e in events["events"])
        except logs.exceptions.ResourceNotFoundException:
            pass
        time.sleep(5)
    raise RuntimeError(f"no log output for task {task_arn}")


def handler(event, _ctx):
    request_type = event["RequestType"]
    if request_type == "Delete":
        return {
            "PhysicalResourceId": event.get(
                "PhysicalResourceId", "headscale-admin-api-key"
            )
        }

    if _current_secret():
        return {"PhysicalResourceId": "headscale-admin-api-key"}

    log_group = os.environ["LOG_GROUP"]
    log_stream_prefix = os.environ["LOG_STREAM_PREFIX"]

    task_arn = _run_task()
    task = _wait_for_stop(task_arn)
    containers = task.get("containers") or []
    exit_code = next(
        (c.get("exitCode") for c in containers if c.get("name") == CONTAINER_NAME), None
    )
    if exit_code != 0:
        raise RuntimeError(f"headscale task exited with code {exit_code}: {task}")

    output = _fetch_log_output(task_arn, log_stream_prefix, log_group)
    api_key = None
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            doc = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "api_key" in doc:
            api_key = doc["api_key"]
            break
    if not api_key:
        raise RuntimeError(f"could not extract api_key from task output: {output!r}")

    sm.put_secret_value(SecretId=SECRET_ID, SecretString=api_key)
    return {"PhysicalResourceId": "headscale-admin-api-key"}
