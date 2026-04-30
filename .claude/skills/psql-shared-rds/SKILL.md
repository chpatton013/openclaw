---
name: psql-shared-rds
description: Open a psql session against the shared Postgres RDS instance, scoped to a named logical database (authentik / headscale / vaultwarden). Resolves the per-database credential secret, looks up the RDS endpoint, and walks through the two reachable connection paths (Tailscale exit-node tunnel, ECS execute-command) plus a one-shot RunTask fallback for environments where neither is available.
allowed-tools: Bash
---

# psql against shared Postgres RDS

Database to connect to: **$ARGUMENTS**

If `$ARGUMENTS` is empty, list the known databases (`authentik`, `headscale`, `vaultwarden`) and ask the user which one to open before continuing.

The shared RDS instance lives in `DataStack` in a **private-isolated** subnet (no NAT egress, no public IP). It is reachable only from inside the VPC or from a peer that has been routed into it — in this repo, that means either the headscale exit node (Tailscale) or an ECS task in the same VPC.

Each consumer stack owns its own logical database + role; the credential lives in a separate Secrets Manager secret in the multi-value format `{"username":"...","password":"..."}`. The DB instance master credential (`data/database`) is **not** what you want for per-app access — use the per-app secret.

---

## Step 1 — Resolve database name → credentials + endpoint

### 1a. Lookup table (from `config.toml`)

| `$ARGUMENTS` | Postgres db name | Postgres role | Credential secret | Nearest consumer service (for execute-command) |
| --- | --- | --- | --- | --- |
| `authentik` | `authentik` | `authentik` | `authentik/database` | Authentik server or worker (`AuthentikStack`) |
| `headscale` | `headscale` | `headscale` | `headscale/database` | Headscale Fargate service (`HeadscaleStack`) |
| `vaultwarden` | `vaultwarden` | `vaultwarden` | `vaultwarden/database` | Vaultwarden Fargate service (`VaultwardenStack`) |

The `name` and the `username` inside the secret are the same string — the DB-init lambda (`assets/lambdas/rds_logical_databases/index.py`) enforces that the secret's `username` matches the role name and refuses to deploy otherwise.

> Reminder: `headscale.exit_node.preauthkey` is the Tailscale exit-node preauthkey, **not** a DB credential. Don't grab it by mistake.

Set these once and reuse below:

```bash
DB="$ARGUMENTS"           # e.g. authentik | headscale | vaultwarden
SECRET="$DB/database"     # always "<db>/database" in this repo
```

### 1b. RDS endpoint

`DataStack` does not export the endpoint to CloudFormation outputs (see `infra/models/data_exports.py`). Look it up directly — there's only one RDS instance in this account, so picking the first is sufficient:

```bash
DB_HOST=$(bin/aws rds describe-db-instances \
  --query 'DBInstances[0].Endpoint.Address' --output text)
DB_PORT=$(bin/aws rds describe-db-instances \
  --query 'DBInstances[0].Endpoint.Port' --output text)
echo "endpoint=$DB_HOST:$DB_PORT"
```

If you've spun up a side-experiment instance and there are multiple, list them and pick the right one (the DataStack instance lives in the foundation VPC):

```bash
bin/aws rds describe-db-instances \
  --query 'DBInstances[*].{Id:DBInstanceIdentifier,Engine:Engine,Endpoint:Endpoint.Address,Port:Endpoint.Port,VpcId:DBSubnetGroup.VpcId}' \
  --output table
```

### 1c. Pull the credentials

Multi-value secret, fetched once and parsed with `jq`:

```bash
CREDS=$(bin/aws secretsmanager get-secret-value \
  --secret-id "$SECRET" --query SecretString --output text)
DB_USER=$(echo "$CREDS" | jq -r .username)
DB_PASS=$(echo "$CREDS" | jq -r .password)
```

Sanity-check (don't print the password):

```bash
echo "user=$DB_USER db=$DB host=$DB_HOST port=$DB_PORT pass_len=${#DB_PASS}"
```

---

## Step 2 — Pick a connection path

Try in this order. Stop at the first one that works.

### Path A — Direct from your laptop, via the headscale exit node (Tailscale)

**When this works:** your laptop is logged into the headscale tailnet *and* the `aws-exit` machine is online and serving as an exit node, **or** you have its advertised subnet route accepted (the exit node advertises the VPC CIDR via `--advertise-exit-node`, so RDS's private IP is routable through it).

**Test reachability first** — RDS endpoints resolve to private IPs that are only routable when the tunnel is up:

```bash
# Should resolve to a 10.x.x.x or 172.16.x.x address
dig +short "$DB_HOST"
# Should not hang. Ctrl-C immediately if it does — Tailscale isn't routing you in.
nc -zv -w 3 "$DB_HOST" "$DB_PORT"
```

If both succeed, connect locally. RDS requires SSL:

```bash
PGPASSWORD="$DB_PASS" psql \
  "host=$DB_HOST port=$DB_PORT dbname=$DB user=$DB_USER sslmode=require"
```

If `psql` isn't installed locally: `brew install libpq && brew link --force libpq` (macOS).

### Path B — Via ECS execute-command (no Tailscale required)

See the `shell-into-service` skill for the canonical cluster / service / task / container resolution flow — same pattern is used here. Pick a running task in the **same** consumer stack (security-group ingress to RDS is granted per-consumer, so use the service that matches the database you're connecting to):

```bash
CLUSTER=$(bin/aws ecs list-clusters \
  --query 'clusterArns[?contains(@, `Foundation`) || contains(@, `foundation`)] | [0]' \
  --output text)

# Pick a service whose name contains the db name (case-insensitive). The
# Authentik stack has two services (server + worker); either works.
SERVICE=$(bin/aws ecs list-services --cluster "$CLUSTER" \
  --query "serviceArns[?contains(to_string(@), '$DB') || contains(to_string(@), '$(echo $DB | tr a-z A-Z)')]" \
  --output text | awk '{print $1}')

TASK=$(bin/aws ecs list-tasks --cluster "$CLUSTER" --service-name "$SERVICE" \
  --desired-status RUNNING --query 'taskArns[0]' --output text)
TASK_ID="${TASK##*/}"

# Identify the app container name (skip *-init / *-noise-init sidecars).
CONTAINER=$(bin/aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ID" \
  --query 'tasks[0].containers[?!contains(name, `Init`) && !contains(name, `init`)].name | [0]' \
  --output text)
```

Open an interactive shell:

```bash
bin/aws ecs execute-command --cluster "$CLUSTER" --task "$TASK_ID" \
  --container "$CONTAINER" --interactive --command "/bin/sh"
```

Inside the container, install the postgres client if it isn't already there. Image base varies by service:

- **Authentik server/worker** (`goauthentik/server`): Debian-based; `apt-get update && apt-get install -y postgresql-client`. May lack `apt-get` in the running container — use Path C if so.
- **Headscale** (`juanfont/headscale`): distroless. No shell, can't `exec` in. Use Path C.
- **Vaultwarden** (`vaultwarden/server`): Debian slim; `apt-get update && apt-get install -y postgresql-client`.

Some Alpine-based images (older Authentik builds, init sidecars) use `apk add postgresql-client` instead.

Then connect — the relevant credentials are already in the container as env vars (the container's secret bindings — see each `*_stack.py`):

| Service | DB host env | DB port env | DB name env | DB user env | DB password env |
| --- | --- | --- | --- | --- | --- |
| Authentik (server / worker) | `AUTHENTIK_POSTGRESQL__HOST` | `AUTHENTIK_POSTGRESQL__PORT` | `AUTHENTIK_POSTGRESQL__NAME` | `AUTHENTIK_POSTGRESQL__USER` | `AUTHENTIK_POSTGRESQL__PASSWORD` |
| Headscale | `HEADSCALE_DATABASE_POSTGRES_HOST` | `HEADSCALE_DATABASE_POSTGRES_PORT` | `HEADSCALE_DATABASE_POSTGRES_NAME` | `HEADSCALE_DATABASE_POSTGRES_USER` | `HEADSCALE_DATABASE_POSTGRES_PASS` |
| Vaultwarden | `DB_HOST` | `DB_PORT` | `DB_NAME` | `DB_USER` | `DB_PASSWORD` |

So inside the container the one-liner is e.g. (Authentik):

```sh
PGPASSWORD="$AUTHENTIK_POSTGRESQL__PASSWORD" psql \
  "host=$AUTHENTIK_POSTGRESQL__HOST port=$AUTHENTIK_POSTGRESQL__PORT \
   dbname=$AUTHENTIK_POSTGRESQL__NAME user=$AUTHENTIK_POSTGRESQL__USER \
   sslmode=require"
```

If exporting `PGPASSWORD` is undesirable, drop it and pass `-W` to be prompted:

```sh
psql "host=...sslmode=require" -W
```

### Path C — One-shot ECS RunTask with a postgres-client image (fallback)

Use this when:
- the consumer container is distroless (Headscale) or you can't install a client in it,
- you want a clean, scriptable invocation without `exec`'ing into anything,
- you need to run a one-off SQL statement non-interactively.

This requires a Fargate task definition that uses `public.ecr.aws/docker/library/postgres:16-alpine` (which ships `psql`) and runs in a security group that the RDS instance's security group accepts. The cleanest approach is to register a throwaway task definition in the existing cluster, reusing one of the consumer services' security groups (which already has DB ingress).

Quick path — register, run, and tail logs:

```bash
# 1. Discover ids.
CLUSTER=$(bin/aws ecs list-clusters \
  --query 'clusterArns[?contains(@, `Foundation`) || contains(@, `foundation`)] | [0]' \
  --output text)
SUBNETS=$(bin/aws ec2 describe-subnets \
  --filters Name=tag:aws-cdk:subnet-type,Values=Private \
  --query 'Subnets[].SubnetId' --output text | tr '\t' ',')
# Use the matching consumer service's SG so RDS already accepts ingress from it.
SG=$(bin/aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" \
  --query 'services[0].networkConfiguration.awsvpcConfiguration.securityGroups[0]' \
  --output text)

# 2. Register a one-off task def. (Reuse an existing exec role if you have one;
# otherwise the simplest path is to point at the consumer service's exec role.)
EXEC_ROLE=$(bin/aws ecs describe-task-definition --task-definition \
  "$(bin/aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" \
     --query 'services[0].taskDefinition' --output text)" \
  --query 'taskDefinition.executionRoleArn' --output text)

cat >/tmp/psql-task.json <<JSON
{
  "family": "psql-oneshot",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "256", "memory": "512",
  "executionRoleArn": "$EXEC_ROLE",
  "containerDefinitions": [{
    "name": "psql",
    "image": "public.ecr.aws/docker/library/postgres:16-alpine",
    "essential": true,
    "command": ["psql", "host=$DB_HOST port=$DB_PORT dbname=$DB user=$DB_USER sslmode=require", "-c", "select version();"],
    "environment": [{"name": "PGPASSWORD", "value": "$DB_PASS"}],
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "/ecs/psql-oneshot",
        "awslogs-region": "us-west-2",
        "awslogs-stream-prefix": "psql",
        "awslogs-create-group": "true"
      }
    }
  }]
}
JSON

bin/aws ecs register-task-definition --cli-input-json file:///tmp/psql-task.json

# 3. Run it.
bin/aws ecs run-task --cluster "$CLUSTER" \
  --launch-type FARGATE --task-definition psql-oneshot \
  --network-configuration "awsvpcConfiguration={subnets=[${SUBNETS}],securityGroups=[$SG],assignPublicIp=DISABLED}" \
  --query 'tasks[0].taskArn' --output text
```

Tail the result via `bin/aws logs get-log-events --log-group-name /ecs/psql-oneshot ...` once the task reaches `STOPPED`. For an interactive session this approach is awkward — prefer Path B if you need a real `psql` REPL.

---

## Step 3 — Common pitfalls

- **`SSL is required`**: pass `sslmode=require` (matches the in-stack settings: Authentik forces `AUTHENTIK_POSTGRESQL__SSLMODE=require`, Headscale sets `HEADSCALE_DATABASE_POSTGRES_SSL=true`, Vaultwarden builds `?sslmode=require` into `DATABASE_URL`).
- **`role does not exist`**: the per-db role isn't created until `DataStack`'s `DbInit` custom resource has run successfully. If a freshly added secret hasn't been picked up, redeploy `DataStack` (or invoke its `DbInitFn` lambda directly) to create the role.
- **`password authentication failed`**: the secret has been rotated but the role's password wasn't updated. The DB-init lambda runs `ALTER ROLE ... PASSWORD ...` on every deploy — redeploy `DataStack` to re-sync.
- **`no route to host` from laptop**: Tailscale tunnel down or exit-node task not running. Check the `debug-exit-node` skill before retrying Path A; in the meantime use Path B.
- **`could not connect: Connection refused` from inside ECS task**: the consumer's security group must already permit egress to the DB SG (it's granted in each consumer stack via `data.database.grant_connect(...)`); if it doesn't, you picked a service that isn't actually wired up to that DB.
