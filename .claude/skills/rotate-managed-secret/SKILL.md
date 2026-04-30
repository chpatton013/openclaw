---
name: rotate-managed-secret
description: Recover a Lambda-managed Secrets Manager secret in this repo when its stored value is orphaned (e.g. a real-but-rejected preauthkey or admin API key after a Headscale user rename, DB rename, or restored backup). Resets the secret to the `{"secret":"pending"}` placeholder, invokes the managing Lambda's Custom Resource handler synchronously to repopulate it, verifies the new value, then force-new-deployments each consuming ECS service so it picks up the rotated value.
allowed-tools: Bash
---

# Rotate a Lambda-managed Secret

Secret to rotate: **$ARGUMENTS**

If `$ARGUMENTS` is empty, ask the user which secret. It must be one of the
managed secrets in the table below.

## Managed secrets in this repo

| Secret name                          | Managing Lambda (logical ID) | Lambda dir (asset)                  | Consuming ECS services that need force-new-deployment        |
| ------------------------------------ | ---------------------------- | ----------------------------------- | ------------------------------------------------------------ |
| `headscale/admin-api-key`            | `*-AdminApiKeyFn-*`          | `headscale_admin_api_key`           | Headplane service (init container fetches it via AWS CLI)    |
| `headscale/exit-node/preauthkey`     | `*-ExitNodePreauthkeyFn-*`   | `headscale_exit_node_preauthkey`    | Exit-node service (`TS_AUTHKEY` injected via `ecs.Secret`)   |

The two preauthkey-related Lambdas (`*-ExitNodeRoutesFn-*` and
`*-ExitNodePreauthkeyFn-*` itself) also read `headscale/admin-api-key` at
runtime, but they fetch fresh on every invocation — no redeploy needed.

If you don't know which is which, grep `assets/lambdas/*/index.py` for
`sm.put_secret_value` — those are the managed secrets; everything else is
operator-populated.

## Step 1 — Resolve the managing Lambda function name

```bash
# Pick the right pattern from the table for the secret you're rotating.
PATTERN='ExitNodePreauthkeyFn'   # or 'AdminApiKeyFn'

FN=$(bin/aws lambda list-functions \
  --query "Functions[?contains(FunctionName, \`${PATTERN}\`)].FunctionName | [0]" \
  --output text)
echo "$FN"   # sanity check: non-empty, contains the pattern
```

## Step 2 — Reset the secret to the placeholder

```bash
SECRET_ID="$ARGUMENTS"

bin/aws secretsmanager put-secret-value \
  --secret-id "$SECRET_ID" \
  --secret-string '{"secret":"pending"}'
```

## Step 3 — Invoke the Lambda with a synthetic Update event

The Lambda is a CDK Custom Resource handler — it dispatches on
`event["RequestType"]`. Use `Update` so it runs the create/refresh path
(not `Delete`). `bin/aws` is awscli v1; pass the payload via `file://`,
not `--cli-binary-format`.

```bash
# PhysicalResourceId values are hardcoded in the lambdas:
#   headscale-admin-api-key       (headscale_admin_api_key)
#   headscale-exit-node-preauthkey (headscale_exit_node_preauthkey)
PHYS_ID='headscale-exit-node-preauthkey'   # adjust per secret

cat > /tmp/rotate-event.json <<EOF
{"RequestType":"Update","PhysicalResourceId":"${PHYS_ID}","ResourceProperties":{}}
EOF

bin/aws lambda invoke \
  --function-name "$FN" \
  --payload file:///tmp/rotate-event.json \
  /tmp/rotate-out.json

cat /tmp/rotate-out.json
# Expect: {"PhysicalResourceId":"<PHYS_ID>"}
# Anything with "errorMessage"/"errorType" means the lambda raised — read it.
```

Tail the lambda's logs if the invocation reports an error (`bin/aws` is
awscli v1, no `logs tail` subcommand — use describe-streams + get-events):

```bash
LG="/aws/lambda/$FN"
STREAM=$(bin/aws logs describe-log-streams --log-group-name "$LG" \
  --order-by LastEventTime --descending --max-items 1 \
  --query 'logStreams[0].logStreamName' --output text)
bin/aws logs get-log-events --log-group-name "$LG" --log-stream-name "$STREAM" \
  --limit 100 --query 'events[].message' --output text | tr '\t' '\n'
```

## Step 4 — Verify the secret holds a real value

The placeholder is the literal string `pending`. A real preauthkey is ~48
chars; a real headscale admin API key is ~72 chars. Anything substantially
longer than `pending` and not equal to it is fine.

```bash
bin/aws secretsmanager get-secret-value \
  --secret-id "$SECRET_ID" \
  --query SecretString --output text \
  | python3 -c "import json,sys; v=json.load(sys.stdin)['secret']; print('len=',len(v),'placeholder=',v=='pending')"
# Expect: len=<big number> placeholder=False
```

If `placeholder=True`, the lambda short-circuited — re-read its logs from
Step 3 to find the exception or the early-return reason.

## Step 5 — Force-new-deployment on each consuming ECS service

Cluster + service resolution is per-secret because Headplane lives in the
foundation cluster while the exit node has its own cluster:

```bash
# headscale/exit-node/preauthkey → exit-node service in ExitNodeCluster
CLUSTER=$(bin/aws ecs list-clusters \
  --query 'clusterArns[?contains(@, `ExitNodeCluster`)] | [0]' --output text)
SERVICE=$(bin/aws ecs list-services --cluster "$CLUSTER" \
  --query 'serviceArns[?contains(@, `ExitNodeService`)] | [0]' --output text)
```

```bash
# headscale/admin-api-key → Headplane service in foundation cluster
CLUSTER=$(bin/aws ecs list-clusters \
  --query 'clusterArns[?contains(@, `FoundationCluster`)] | [0]' --output text)
SERVICE=$(bin/aws ecs list-services --cluster "$CLUSTER" \
  --query 'serviceArns[?contains(@, `HeadplaneService`)] | [0]' --output text)
```

Then in either case:

```bash
bin/aws ecs update-service \
  --cluster "$CLUSTER" --service "$SERVICE" \
  --force-new-deployment \
  --query 'service.{name:serviceName,deployments:deployments[].{status:status,rollout:rolloutState}}'
```

Repeat for each consuming service from the table.

## Step 6 — Verify recovery

```bash
bin/aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" \
  --query 'services[0].{running:runningCount,desired:desiredCount,deployments:deployments[].{status:status,rollout:rolloutState}}'
```

Healthy when `runningCount == desiredCount` and the latest deployment's
`rolloutState` is `COMPLETED`.

For the exit-node case specifically, also confirm the new task's logs show
`machineAuthorized=true` — see `debug-exit-node` skill if not.

## Notes

- Don't `--amend` or stop mid-flow: the placeholder state will block ECS
  tasks from starting. If you reset to `pending` and the Lambda invoke
  fails, fix the Lambda error and re-run Step 3 before walking away.
- The Lambda's own idempotency check (e.g. `_stored_key_belongs_to_user`
  in `headscale_exit_node_preauthkey/index.py`) is what makes the
  rotation safe — it only writes a new value when the stored one is
  missing, the placeholder, or orphaned. Step 2's reset is what forces
  the write path.
- Both managed lambdas hardcode their `PhysicalResourceId`; using the
  wrong one in the synthetic event is harmless (the handler returns the
  hardcoded value) but report-confusing.
