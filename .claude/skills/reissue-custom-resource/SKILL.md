---
name: reissue-custom-resource
description: Re-run a Custom Resource in this repo's CDK stacks. Use when an operator needs to force a Custom Resource handler to execute again — either through the audit-friendly Trigger-bump path (`bin/cdk deploy`) or, when the stack is wedged, by synthetically invoking the underlying Lambda with a Create/Update/Delete event payload.
allowed-tools: Bash
---

# Re-issue a Custom Resource

Custom Resource to re-run: **$ARGUMENTS**

If `$ARGUMENTS` is empty, list the catalog below and ask the user which one they
want before proceeding. Match `$ARGUMENTS` against the **Construct id** column.

---

## Catalog

All four entries below correspond to a literal `CustomResource(...)` instance
backed by a `cr.Provider` whose `on_event_handler` is a `PythonFunction`. Every
handler matches the standard CloudFormation Custom Resource event shape — it
reads `event["RequestType"]` (one of `"Create"`, `"Update"`, `"Delete"`) and
returns `{"PhysicalResourceId": "<id>"}`. The `Delete` branch is always a no-op
that just echoes the physical id back.

| Construct id | Stack | What it does | Lambda nickname (CDK id) | Re-run path |
|---|---|---|---|---|
| `DbInit` | `DataStack` (`infra/stacks/data_stack.py`) | Connects to the RDS Postgres master with `pg8000`, then for each `DbConfig` ensures a logical database + role exist and the role's password matches its own Secrets Manager secret. | `DbInitFn` (`assets/lambdas/rds_logical_databases`) | **Path A**, but no `Trigger` property. Re-runs naturally when `Host`, `Port`, `MasterSecretArn`, or the `Databases` list change. To force a re-run without changing inputs, use **Path B** (synthetic invoke) or temporarily add a `Trigger` property and bump it. |
| `AdminApiKey` | `HeadscaleStack` (`infra/stacks/headscale_stack.py`) | Runs the `headscale_api_key` ECS task once, parses the API key from its logs, and stores it in `headscale/admin-api-key` (Secrets Manager). Skips if the secret is already populated (non-`pending`). | `AdminApiKeyFn` (`assets/lambdas/headscale_admin_api_key`) | **Path A** — bump `properties={"Trigger": "vN"}` (currently `v2`). Note: handler short-circuits if the secret is already non-placeholder, so a Trigger bump alone won't rotate the key — first reset the secret to `{"secret":"pending"}` (see `rotate-managed-secret` skill). |
| `ExitNodePreauthkey` | `HeadscaleStack` | Calls Headscale REST API to ensure the preauthkey user exists, deletes stale offline `aws-exit*` nodes, and stores a fresh preauthkey in `headscale/exit-node/preauthkey` if the stored one is missing or doesn't belong to the current user. | `ExitNodePreauthkeyFn` (`assets/lambdas/headscale_exit_node_preauthkey`) | **Path A** — bump `properties={"Trigger": "vN"}` (currently `v5`). Like `AdminApiKey`, the handler is idempotent on a valid stored key; reset the secret to `{"secret":"pending"}` to force a regen. |
| `ExitNodeRoutes` | `HeadscaleStack` | Polls Headscale until `aws-exit` is online with advertised routes, then runs a one-shot ECS task that invokes `headscale nodes approve-routes` (gRPC, no REST endpoint in 0.26). | `ExitNodeRoutesFn` (`assets/lambdas/headscale_exit_node_routes`) | **Path A** — bump `properties={"Trigger": "vN"}` (currently `v4`). |

---

## Path A — bump `Trigger` and `bin/cdk deploy` (default)

Use for routine re-runs: config drift refresh, picking up a code change in the
handler, recovery after a `continue-update-rollback --resources-to-skip` (the
skip marks the existing `Trigger` value as applied, so you must change it to
make CFN re-invoke).

1. Edit the stack file and increment the version string. Example for
   `ExitNodeRoutes`:

   ```python
   # infra/stacks/headscale_stack.py
   exit_node_routes_resource = CustomResource(
       self,
       "ExitNodeRoutes",
       service_token=exit_node_routes_provider.service_token,
       properties={"Trigger": "v5"},   # was "v4"
   )
   ```

2. Deploy. From the repo root:

   ```bash
   bin/cdk diff HeadscaleStack    # confirm only the Trigger change
   bin/cdk deploy HeadscaleStack
   ```

   Use `DataStack` for `DbInit`, `HeadscaleStack` for the other three.

3. Watch the deployment. The Custom Resource event in CloudFormation will show
   the new `Trigger` value; the handler will run with `RequestType: Update`.

**Audit advantage:** the Trigger bump shows up in `git log` and the next CFN
diff. Prefer this path whenever the stack is healthy.

---

## Path B — synthetic Lambda invoke (incident recovery)

Use only when Path A is blocked — typically a wedged stack
(`UPDATE_ROLLBACK_FAILED`, in-progress deploy you can't replace, or you need to
re-run a handler *before* `cdk deploy` will succeed). This bypasses
CloudFormation entirely: the side effect (secret write, DB role mutation,
routes approved) happens, but CloudFormation's recorded view of the resource
does not change. A subsequent `cdk deploy` may still want to re-invoke the
handler — that's expected.

Resolve the deployed Lambda function name (CDK appends a hash) and send a
synthetic Custom Resource event:

```bash
# Pick the right nickname substring for your target:
#   DbInit              → DbInitFn
#   AdminApiKey         → AdminApiKeyFn
#   ExitNodePreauthkey  → ExitNodePreauthkeyFn
#   ExitNodeRoutes      → ExitNodeRoutesFn
FN=$(bin/aws lambda list-functions \
  --query 'Functions[?contains(FunctionName, `ExitNodeRoutesFn`)].FunctionName | [0]' \
  --output text)

# Build the event payload as a file (awscli v1 reads it raw — no
# --cli-binary-format flag needed/supported).
echo '{"RequestType":"Update","PhysicalResourceId":"headscale-exit-node-routes","ResourceProperties":{}}' \
  > /tmp/event.json

bin/aws lambda invoke \
  --function-name "$FN" \
  --payload file:///tmp/event.json \
  /tmp/out.json

cat /tmp/out.json
# Expect: {"PhysicalResourceId":"<id>"} matching the Delete-branch fallback
# in the handler (see table below).
```

**Physical resource ids** to use in the synthetic event (these are what each
handler returns and what its `Delete` branch echoes back):

| Construct id | `PhysicalResourceId` |
|---|---|
| `DbInit` | `rds-logical-databases` |
| `AdminApiKey` | `headscale-admin-api-key` |
| `ExitNodePreauthkey` | `headscale-exit-node-preauthkey` |
| `ExitNodeRoutes` | `headscale-exit-node-routes` |

**`ResourceProperties` requirements**:
- `DbInit` requires `Host`, `Port`, `MasterSecretArn`, `Databases`. The other
  three handlers ignore `ResourceProperties` entirely (they read everything
  from environment variables baked in at deploy time), so `{}` is fine.
- For `DbInit`, copy the values from the latest CloudFormation event for the
  resource, or read them off the deployed RDS instance and the master secret.

**Delete-only invocations** (rarely needed): set
`"RequestType":"Delete"`. Every handler's Delete branch is a no-op that just
returns the physical id, so this is safe but also useless — there's no
cleanup logic to trigger.

---

## When to use which

| Situation | Path |
|---|---|
| Routine refresh / config drift / picking up new handler code | **A** (bump Trigger, deploy) |
| After `continue-update-rollback --resources-to-skip <X>` to make CFN re-invoke | **A** (the skip marked the current Trigger as applied — bump it) |
| Stack stuck mid-deploy, can't even run `bin/cdk diff` cleanly | **B** (synthetic invoke to fix the underlying side effect, then unstick the stack) |
| Need to rotate a Lambda-managed secret (`AdminApiKey`, `ExitNodePreauthkey`) | Reset the secret to `{"secret":"pending"}`, then **B** (or **A** after the reset). See the `rotate-managed-secret` skill. |
| `DbInit` failed but the stack is otherwise healthy | **A** (touch the `Databases` list, e.g. add and remove a whitespace change in the stack, or temporarily add a `Trigger` property) — or **B** with a fully-formed `ResourceProperties` payload |

After Path B, when the stack becomes unwedged, do a `bin/cdk deploy` to
reconcile CloudFormation's view with the now-fixed side effect.
