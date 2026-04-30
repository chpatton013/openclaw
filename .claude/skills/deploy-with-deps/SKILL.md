---
name: deploy-with-deps
description: Deploy a stack and its upstream dependencies. Resolves the upstream closure for a named stack from the app-level DAG and emits the right `bin/cdk deploy` invocation (with concurrency flags), so the operator doesn't have to remember which other stacks have to ship alongside it.
allowed-tools: Bash
---

# Deploy a Stack With Its Upstream Dependencies

Stack to deploy: **$ARGUMENTS**

If `$ARGUMENTS` is empty, list the stacks via `bin/cdk ls` and ask the user which one they want:

```bash
bin/cdk ls
```

---

## Why this skill exists

This repo wires its CDK stacks into a DAG by passing producer stacks' `*Exports`
dataclasses into consumer stacks' `*Imports` dataclasses (see `infra/app_builder.py`).

CDK's deploy behavior for a named stack:

- `bin/cdk deploy <stack>` — by default CDK *does* include upstream dependencies, but it
  will pause with an interactive prompt for any dependency that has changes,
  unless you pre-list them on the command line, pass `--require-approval never`, or pass
  `--exclusively` to suppress the dependency walk entirely.
- `bin/cdk deploy --exclusively <stack>` — only that stack; no dependency walk.
- `bin/cdk deploy --all` — every stack in the app, even ones you didn't touch.

Verify the flag yourself if you want:

```bash
bin/cdk deploy --help | grep -E -A1 'exclusively|require-approval|concurrency'
```

The pragmatic recipe: explicitly list the upstream closure on the command line. That makes
the deploy reproducible, avoids the "you forgot DataStack" surprise, and stays away from
the blanket `--all`.

(`bin/cdk` is the dotslash wrapper at `bin/cdk` that just `exec`s `bun x --package aws-cdk cdk "$@"` — no project-specific defaults.)

---

## The DAG (derived from `infra/app_builder.py`)

```
FoundationStack ──────────────┬────────────────────────────────────┐
                              │                                    │
                              ▼                                    ▼
                          DataStack                        WebFingerStack
                              │                            (foundation only)
                ┌─────────────┼──────────────┐
                ▼             ▼              ▼
         AuthentikStack  HeadscaleStack  VaultwardenStack
         (foundation +   (foundation +   (foundation +
          data)           data)           data)

OpenClawStack  (no imports — standalone homelab/EC2 stack)
```

Notes verified against the source:

- `FoundationStack` takes no producer imports.
- `DataStack` imports `FoundationExports`.
- `AuthentikStack`, `HeadscaleStack`, `VaultwardenStack` each import both `FoundationExports` and `DataExports`.
- `WebFingerStack` imports only `FoundationExports`.
- `HeadscaleStack`, `WebFingerStack`, and `AuthentikStack` reference `authentik_issuer_base` as a *derived string from config*, not as an `AuthentikExports` — so AuthentikStack is **not** a CDK dependency of HeadscaleStack/WebFingerStack.
- `OpenClawStack` is constructed with no `imports=` at all — it's standalone.

---

## Common closures (BFS over the DAG)

| Target stack       | Upstream closure (operator should pass these to `deploy`) |
|--------------------|-----------------------------------------------------------|
| `FoundationStack`  | `FoundationStack`                                         |
| `DataStack`        | `FoundationStack DataStack`                               |
| `AuthentikStack`   | `FoundationStack DataStack AuthentikStack`                |
| `WebFingerStack`   | `FoundationStack WebFingerStack`                          |
| `HeadscaleStack`   | `FoundationStack DataStack HeadscaleStack`                |
| `VaultwardenStack` | `FoundationStack DataStack VaultwardenStack`              |
| `OpenClawStack`    | `OpenClawStack`                                           |

---

## Step 1 — Resolve closure

Match `$ARGUMENTS` against the table above and emit the closure list.

If the stack is not in the table, refuse and rerun `bin/cdk ls` to confirm the name.

## Step 2 — Build the deploy command

The repo's README deploy section uses these concurrency flags. Use the same:

```bash
bin/cdk deploy <STACK1> <STACK2> ... \
  --concurrency="$(nproc --all)" \
  --asset-build-concurrency="$(nproc --all)"
```

Concrete commands per target:

```bash
# FoundationStack
bin/cdk deploy FoundationStack \
  --concurrency="$(nproc --all)" --asset-build-concurrency="$(nproc --all)"

# DataStack
bin/cdk deploy FoundationStack DataStack \
  --concurrency="$(nproc --all)" --asset-build-concurrency="$(nproc --all)"

# AuthentikStack
bin/cdk deploy FoundationStack DataStack AuthentikStack \
  --concurrency="$(nproc --all)" --asset-build-concurrency="$(nproc --all)"

# WebFingerStack
bin/cdk deploy FoundationStack WebFingerStack \
  --concurrency="$(nproc --all)" --asset-build-concurrency="$(nproc --all)"

# HeadscaleStack
bin/cdk deploy FoundationStack DataStack HeadscaleStack \
  --concurrency="$(nproc --all)" --asset-build-concurrency="$(nproc --all)"

# VaultwardenStack
bin/cdk deploy FoundationStack DataStack VaultwardenStack \
  --concurrency="$(nproc --all)" --asset-build-concurrency="$(nproc --all)"

# OpenClawStack
bin/cdk deploy OpenClawStack \
  --concurrency="$(nproc --all)" --asset-build-concurrency="$(nproc --all)"
```

## Step 3 — Approval mode

The README's full-deploy command adds `--require-approval never`. That's safe for a
fully autonomous run, but it auto-approves any IAM/security-broadening changes — which
is exactly the sort of thing you want a human to eyeball.

- **Interactive operator at the keyboard** → omit `--require-approval`. CDK prompts on broadening changes; you decide.
- **Headless / CI / "deploy and walk away"** → append `--require-approval never`. You're trusting the diff.

Mention this trade-off when you emit the command; don't add the flag silently.

## Step 4 — What about downstream stacks?

Deploying `FoundationStack` or `DataStack` may rotate values that consumers cache in
their ECS task definitions (DB endpoints, Secrets Manager pointers, etc.). CloudFormation
will not redeploy a downstream stack just because an export changed — and even when the
downstream stack *is* redeployed, its ECS service may still hold the old values until
the next task replacement.

After redeploying a producer:

- If a downstream stack consumes a rotated **secret value**, see the `rotate-managed-secret`
  skill — it handles secret refresh + force-new-deployment of consumer ECS services.
- If a downstream stack consumes a rotated **export** (e.g. RDS endpoint, security group),
  redeploy the downstream stack too (add it to the closure list above) and follow up
  with `aws ecs update-service --cluster ... --service ... --force-new-deployment` for any
  service that didn't get a new task definition revision.

## Step 5 — Run it

Print the resolved command for the user, then run it. If `bin/cdk` complains about an
unknown stack name, fall back to `bin/cdk ls` to show the canonical names.
