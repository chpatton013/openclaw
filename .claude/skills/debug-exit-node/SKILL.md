---
name: debug-exit-node
description: Debug Tailscale exit-node registration / route-approval failures in HeadscaleStack. Covers the canonical symptoms (ECS task crash-looping with "AuthKey not found", deployment circuit breaker tripped, ExitNodeRoutes Custom Resource timing out, stale `aws-exit` registration in Headplane shadowing a new task) and the recovery actions for each.
allowed-tools: Bash
---

# Debug Tailscale Exit Node

Symptom (one or more): ECS service for the exit node is unhealthy, the `aws-exit` machine isn't online in Headplane, the `ExitNodeRoutes` Custom Resource is failing during a `cdk deploy`, or a new task registered with a collision-suffixed name (`aws-exit-XXXXXXX`).

Most issues fall into one of three buckets — diagnose first, then act.

## Step 1 — Resolve names

The cluster, service, and lambda names include CDK-generated hashes. Resolve them:

```bash
CLUSTER=$(aws ecs list-clusters --query 'clusterArns[?contains(@, `ExitNode`)] | [0]' --output text)
SERVICE=$(aws ecs list-services --cluster "$CLUSTER" --query 'serviceArns[0]' --output text)
PREAUTHKEY_FN=$(aws lambda list-functions --query 'Functions[?contains(FunctionName, `ExitNodePreauthkeyFn`)].FunctionName | [0]' --output text)
```

## Step 2 — Inspect the latest task

```bash
TASK=$(aws ecs list-tasks --cluster "$CLUSTER" --service-name "$SERVICE" --query 'taskArns[0]' --output text)
[ "$TASK" = "None" ] && TASK=$(aws ecs list-tasks --cluster "$CLUSTER" --desired-status STOPPED --query 'taskArns | reverse(@) | [0]' --output text)
TASK_ID="${TASK##*/}"

aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ID" \
  --query 'tasks[0].{stopReason:stoppedReason,stopCode:stopCode,containers:containers[].{name:name,exitCode:exitCode,reason:reason}}'

# Tail the container's logs (the only log stream prefix is `exit-node`).
LOG_GROUP=$(aws logs describe-log-groups --query 'logGroups[?contains(logGroupName, `ExitNodeLogGroup`)].logGroupName | [0]' --output text)
aws logs get-log-events --log-group-name "$LOG_GROUP" \
  --log-stream-name "exit-node/Container/$TASK_ID" --limit 100 \
  --query 'events[].message' --output text | tr '\t' '\n'
```

## Step 3 — Match symptom → diagnosis → action

### Logs contain `AuthKey not found` / `tailscale up failed`

The preauthkey in Secrets Manager is orphaned — it's a real key, but for a Headscale user that no longer exists (or with a different name than `headscale.exit_node.preauthkey_user`).

**Common causes:**
- `preauthkey_user` was renamed in `config.toml` after the secret was first populated.
- The user was deleted in Headplane.
- Headscale state was restored from an older backup; the secret outlived the matching DB row.

**Recovery:**

```bash
# 1. Reset the secret to the placeholder so the lambda regenerates.
aws secretsmanager put-secret-value \
  --secret-id headscale/exit-node/preauthkey \
  --secret-string '{"secret":"pending"}'

# 2. Force the preauthkey lambda to run now (don't wait for next deploy).
echo '{"RequestType":"Update","PhysicalResourceId":"headscale-exit-node-preauthkey","ResourceProperties":{}}' \
  > /tmp/preauthkey-event.json
aws lambda invoke --function-name "$PREAUTHKEY_FN" \
  --payload file:///tmp/preauthkey-event.json /tmp/preauthkey-out.json
cat /tmp/preauthkey-out.json  # expect {"PhysicalResourceId":"headscale-exit-node-preauthkey"}

# 3. Verify the secret now holds a real key (length ~48), not "pending".
aws secretsmanager get-secret-value --secret-id headscale/exit-node/preauthkey \
  --query SecretString --output text | python3 -c "import json,sys; print('len=', len(json.load(sys.stdin)['secret']))"

# 4. Force a new ECS deployment so the running task reads the new secret.
aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --force-new-deployment
```

If after this the task still won't register, the EC2 host's `/var/lib/tailscale` directory contains stale node-key state from before the rotation. Terminate the EC2 instance — the ASG will replace it with a clean state directory:

```bash
INSTANCE=$(aws ec2 describe-instances --filters \
    'Name=tag:aws:autoscaling:groupName,Values=*ExitNode*' \
    'Name=instance-state-name,Values=running' \
    --query 'Reservations[].Instances[].InstanceId | [0]' --output text)
aws ec2 terminate-instances --instance-ids "$INSTANCE"
```

### `aws-exit` is online but a stale offline `aws-exit-XXXXXXX` exists in Headplane

Cosmetic. The preauthkey lambda's `_delete_stale_nodes` cleans these up on next deploy (matches `aws-exit` and any `aws-exit-` prefix). Delete manually in Headplane if it bothers you.

### `aws-exit` is online but `ExitNodeRoutes` Custom Resource fails / hangs

The routes lambda runs an ECS one-shot task to call `headscale nodes approve-routes` (Headscale 0.26 has no REST endpoint for this). Two ways it breaks:

- **Lambda times out with `Node 'aws-exit' not found or has no routes`**: `_find_node` is matching a stale offline node first and returning early "all routes approved". This was a real bug — `_find_node` should match `online == True` AND `givenName == NODE_HOSTNAME or givenName.startswith(NODE_HOSTNAME + "-")`. Confirm the deployed lambda code has the online filter; if not, that's the fix.

- **Lambda fails with `Parameter validation failed: Unknown parameter in overrides.containerOverrides[0]: "entryPoint"`**: ECS `RunTask.containerOverrides` does NOT accept `entryPoint`; only `command`. The api_key Dockerfile must use `CMD` (not `ENTRYPOINT`) so the lambda can override via `command=["/usr/local/bin/approve-routes"]`. Check `assets/headscale_api_key/Dockerfile` and `assets/lambdas/headscale_exit_node_routes/index.py`.

### Stack stuck in `UPDATE_ROLLBACK_FAILED` because of `ExitNodeRoutes`

```bash
aws cloudformation continue-update-rollback \
  --stack-name HeadscaleStack \
  --resources-to-skip ExitNodeRoutes
```

The skip marks the resource as if its update succeeded; you'll need to bump the `ExitNodeRoutes` `Trigger` property in `headscale_stack.py` (e.g. `v4` → `v5`) to re-run it on the next deploy.

### Deployment circuit breaker tripped (`runningCount=0`, `desiredCount=1`)

Once the underlying problem is fixed (preauthkey rotated, instance replaced, etc.), reset the deployment with `--force-new-deployment`. The circuit breaker resets when a new deployment starts.

## Step 4 — Verify recovery

```bash
# Wait for the deployment to settle, then check task health.
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" \
  --query 'services[0].{running:runningCount,desired:desiredCount,deployments:deployments[].{status:status,rollout:rolloutState}}'

# Tail the new task's logs and look for `machineAuthorized=true`.
TASK_ID=$(aws ecs list-tasks --cluster "$CLUSTER" --service-name "$SERVICE" \
  --query 'taskArns[0]' --output text)
TASK_ID="${TASK_ID##*/}"
aws logs get-log-events --log-group-name "$LOG_GROUP" \
  --log-stream-name "exit-node/Container/$TASK_ID" --start-from-head --limit 200 \
  --query 'events[].message' --output text | tr '\t' '\n' | grep -E 'authkey|register|machineAuthorized|backend error'
```

The exit node is healthy when:
- ECS service: `runningCount == desiredCount` and rollout state is `COMPLETED`.
- Logs: `RegisterReq: got response; ... machineAuthorized=true`.
- Headplane: `aws-exit` shows online with the advertised exit-node route approved.
