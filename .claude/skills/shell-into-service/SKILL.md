---
name: shell-into-service
description: Open an interactive shell in a running ECS task for a named service via `aws ecs execute-command`. Resolves cluster + service + task ARNs by nickname and picks the right shell binary for the container image.
allowed-tools: Bash
---

# Shell Into a Running ECS Service

Service nickname: **$ARGUMENTS**

If `$ARGUMENTS` is empty, list the known nicknames from the table below and ask the user which one to target.

Every ECS service in this repo is built through `PrivateEgressFargateService` or `PrivateEgressEc2Service`, both of which set `enable_execute_command=True` and grant the four `ssmmessages:*` actions on the task role. Execute-command is wired by construction; if it fails, something else is wrong.

---

## Prerequisite: Session Manager plugin

`aws ecs execute-command` shells out to the local Session Manager plugin. If it's missing you'll see:

```
SessionManagerPlugin is not found. Please refer to SessionManager Documentation here: ...
```

Install it (one-time):

```bash
brew install --cask session-manager-plugin
```

Verify:

```bash
session-manager-plugin --version
```

Docs: https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html

---

## Lookup table

| Nickname            | Cluster              | Service selector (in `list-services`) | Container name | Shell          | Image                              |
| ------------------- | -------------------- | -------------------------------------- | -------------- | -------------- | ---------------------------------- |
| `authentik-server`  | `foundation` cluster | `AuthentikStack-ServerService`         | `Container`    | `/bin/bash`    | `goauthentik/server` (Debian)      |
| `authentik-worker`  | `foundation` cluster | `AuthentikStack-WorkerService`         | `Container`    | `/bin/bash`    | `goauthentik/server` (Debian)      |
| `headscale`         | `foundation` cluster | `HeadscaleStack-HeadscaleService`      | `Container`    | none[^1]       | `juanfont/headscale` (distroless)  |
| `headplane`         | `foundation` cluster | `HeadscaleStack-HeadplaneService`      | `Container`    | none[^2]       | `tale/headplane` (distroless)      |
| `vaultwarden`       | `foundation` cluster | `VaultwardenStack-Service`             | `Container`    | `/bin/bash`    | `vaultwarden/server` (Debian)      |
| `aws-exit`          | `ExitNodeCluster`    | `HeadscaleStack-ExitNode`              | `Container`    | `/bin/sh`      | `tailscale/tailscale` (Alpine)     |

[^1]: The upstream `juanfont/headscale` image is `FROM scratch` — there is no shell, no `ls`, nothing. `aws ecs execute-command` will fail with `OCI runtime exec failed: ... no such file or directory`. To poke at headscale state, use the api_key one-off task definition (see `headscale_stack.py` `ApiKeyTaskDefn`) which bundles the headscale binary on Alpine.

[^2]: `tale/headplane` is also distroless. There's a Node binary at `/nodejs/bin/node` you can exec for one-shot scripts, but no interactive shell. Try `--command /nodejs/bin/node` if you need a REPL.

The "Service selector" column is what you'll grep `list-services` for — the actual service ARN includes a CDK-generated suffix.

---

## Step 1 — Resolve cluster ARN

Most services live in the `foundation` cluster (created in `FoundationStack`). The exit node is the only exception — it has its own cluster (`ExitNodeCluster`) created inside `PrivateEgressEc2Service`.

```bash
# foundation cluster (used by everything except aws-exit)
CLUSTER=$(bin/aws ecs list-clusters \
  --query 'clusterArns[?contains(@, `FoundationStack`)] | [0]' --output text)

# OR for aws-exit:
CLUSTER=$(bin/aws ecs list-clusters \
  --query 'clusterArns[?contains(@, `ExitNode`)] | [0]' --output text)
```

If you get `None`, the stack isn't deployed.

## Step 2 — Resolve service ARN

Use the selector substring from the table:

```bash
SELECTOR="HeadscaleStack-HeadscaleService"   # example, swap per nickname
SERVICE=$(bin/aws ecs list-services --cluster "$CLUSTER" \
  --query "serviceArns[?contains(@, \`$SELECTOR\`)] | [0]" --output text)
```

If `$SERVICE` is `None`, the service isn't in this cluster — double-check the cluster choice in Step 1 (exit node lives in its own cluster).

## Step 3 — Resolve a running task ARN

```bash
TASK=$(bin/aws ecs list-tasks --cluster "$CLUSTER" --service-name "$SERVICE" \
  --desired-status RUNNING --query 'taskArns[0]' --output text)
```

If `$TASK` is `None`: the service has no running tasks. Either `desiredCount=0`, or every task is crash-looping (circuit breaker tripped). Check service health:

```bash
bin/aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" \
  --query 'services[0].{running:runningCount,desired:desiredCount,events:events[0:3]}'
```

Recovery if a deployment is stuck:

```bash
bin/aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --force-new-deployment
```

## Step 4 — Open the shell

```bash
CONTAINER="Container"   # always "Container" in this repo
SHELL_BIN="/bin/bash"   # per the lookup table

bin/aws ecs execute-command \
  --cluster "$CLUSTER" \
  --task "$TASK" \
  --container "$CONTAINER" \
  --interactive \
  --command "$SHELL_BIN"
```

You're in. `exit` to leave.

---

## Failure modes

### `SessionManagerPlugin is not found`

See prerequisite above. Install with `brew install --cask session-manager-plugin`.

### `OCI runtime exec failed: ... "/bin/bash": stat /bin/bash: no such file or directory`

Wrong shell for the image. Try `/bin/sh`. If that also fails, the image is distroless (see footnotes for `headscale`/`headplane`) — you cannot get an interactive shell. Use a sidecar or rebuild the image with a debug layer.

### `An error occurred (InvalidParameterException) when calling the ExecuteCommand operation: The execute command failed because execute command was not enabled when the task was run`

Shouldn't happen in this repo — both service constructs enable it unconditionally. If it does, the running task was started from a stale task definition that predates the `enable_execute_command=True` line. Force a new deployment:

```bash
bin/aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --force-new-deployment
```

### `An error occurred ... The container <name> was not found in the task`

Wrong container name. Every service in this repo names its main container literally `Container` (the construct passes `"Container"` as the construct id to `add_container`). If you're targeting an init/sidecar instead, list them:

```bash
bin/aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK" \
  --query 'tasks[0].containers[].name'
```

### `An error occurred (TargetNotConnectedException) when calling the ExecuteCommand operation`

The SSM agent inside the task hasn't registered yet (typical in the first ~30s after task start) or the task is shutting down. Wait, then retry, or pick a different task ARN if more than one is running.

### Task starts but immediately exits

Not an execute-command problem — the container is crashing. Use the `debug-stack` skill to inspect logs and stop reasons.
