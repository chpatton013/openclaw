---
name: add-stack-scaffold
description: Scaffold a new CDK stack following repo conventions. Walks the operator through creating the stack module, *Config (and optional *Exports) model, app.py wiring, config.toml block, bootstrap secrets, and tests, with template snippets pulled from existing stacks (FoundationStack, DataStack, AuthentikStack, HeadscaleStack, VaultwardenStack, WebFingerStack).
allowed-tools: Bash
---

# Add a New Stack Scaffold

New stack nickname: **$ARGUMENTS**

If `$ARGUMENTS` is empty, ask the operator for a nickname (lowercase, snake_case)
before proceeding. Examples from the README's planned list: `synapse`, `mail`,
`searxng`, `nextcloud`, `forgejo`. The nickname drives every name below:

- module:        `infra/stacks/<name>_stack.py`
- config model:  `infra/models/<name>_config.py`
- exports model: `infra/models/<name>_exports.py` *(only if this stack produces shared resources)*
- class names:   `<Name>Stack`, `<Name>Imports`, `<Name>Config`, `<Name>Exports`
- toml section:  `[<name>]` (with nested tables like `[<name>.db]`, `[<name>.task]`)
- stack id:      `<Name>Stack` (used in `app.py` and `bin/cdk synth <Name>Stack`)

Throughout this recipe, replace `<name>` / `<Name>` with the nickname in
snake_case / PascalCase respectively.

Before writing code, ask the operator a few questions so the templates pulled
below match what they actually need. If the answer to a given question is "no",
just delete the corresponding section from the template you copy.

1. **Public HTTP endpoint?** If yes, copy the `PublicHttpAlb` block (like
   `VaultwardenStack`). If it's a webhook/API rather than a webapp, prefer
   `PublicHttpApi` (like `WebFingerStack`).
2. **Postgres database?** If yes, the new stack consumes `DataExports` and
   needs a `[<name>.db]` table + a `<name>/database` bootstrap secret. The new
   `DbConfig` must also be appended to the `databases=[...]` list passed to
   `DataImports` in `app.py` so the data init Lambda creates the logical DB.
3. **Persistent files (uploads, attachments, etc.)?** If yes, follow the EFS
   pattern in `VaultwardenStack` (`efs.FileSystem` + access point + ECS volume
   + `add_mount_points`).
4. **Authentik OIDC SSO?** If yes:
   - Add an `authentik/oidc/<name>` secret to bootstrap (`{"client_id":"...","client_secret":"..."}` shape).
   - Pass an `authentik_issuer_base` string into `<Name>Imports` (see
     `HeadscaleStack`/`WebFingerStack` for the pattern).
   - Add the redirect URI as an extra `Imports` field — `AuthentikStack` reads
     these to seed its blueprint env vars.
   - Coordinate with the planned `add-authentik-app` skill for the blueprint
     side.
5. **Compute kind?** Almost everything is `PrivateEgressFargateService`. Only
   reach for `PrivateEgressEc2Service` when the workload needs the host kernel
   (WireGuard, NFS server, GPU passthrough, etc. — see the exit node in
   `HeadscaleStack`). Bare `ec2.Instance` is only for `OpenClawStack`.
6. **Producer or consumer?** If any *other* stack will need to reference
   resources from this one (a shared cluster, a hosted zone, a queue, etc.),
   write a `<Name>Exports` model. Most service stacks are pure consumers and do
   not need one. Look at `FoundationExports` and `DataExports` for the only
   producers in the repo today.

---

## Step 1 — Create `infra/stacks/<name>_stack.py`

Use the `VaultwardenStack` shape as the canonical "public-HTTP + Postgres +
SMTP + EFS" service. Trim sections you don't need.

```python
from dataclasses import dataclass

from aws_cdk import (
    Duration,
    Stack,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct

from ..constructs.fargate_service import PrivateEgressFargateService
from ..constructs.public_http_alb import PublicHttpAlb
from ..models.data_exports import DataExports
from ..models.foundation_exports import FoundationExports
from ..models.<name>_config import <Name>Config

<NAME>_HTTP_PORT = 8080  # whatever the upstream image listens on


@dataclass(frozen=True)
class <Name>Imports:
    cfg: <Name>Config
    foundation: FoundationExports
    data: DataExports
    # Optional, if this stack uses Authentik SSO. Pass the redirect URI in too
    # so AuthentikStack can seed its blueprint with a matching value.
    # authentik_issuer_base: str


class <Name>Stack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        imports: <Name>Imports,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cfg = imports.cfg
        foundation = imports.foundation
        data = imports.data

        fqdn = f"{cfg.subdomain}.{foundation.public_domain}"

        ###
        # Secrets
        db_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "DbSecret", cfg.db.secret_name
        )

        ###
        # Service
        environment = {
            "DB_HOST": data.database.instance.db_instance_endpoint_address,
            "DB_PORT": str(data.database.port),
            "DB_NAME": cfg.db.name,
            "LOG_LEVEL": "info",
        }
        secrets = {
            "DB_USER": ecs.Secret.from_secrets_manager(db_secret, "username"),
            "DB_PASSWORD": ecs.Secret.from_secrets_manager(db_secret, "password"),
        }

        image = ecs.ContainerImage.from_registry(
            f"{foundation.dockerhub_mirror_base}/<upstream>/<image>:{cfg.image_version}"
        )

        service = PrivateEgressFargateService(
            self,
            "Service",
            stream_prefix="<name>",
            cpu=cfg.task.cpu,
            memory_limit_mib=cfg.task.memory_limit_mib,
            desired_count=cfg.task.desired_count,
            min_healthy_percent=cfg.task.min_healthy_percent,
            vpc=foundation.vpc,
            cluster=foundation.cluster,
            container_kwargs=dict(
                image=image,
                port_mappings=[
                    ecs.PortMapping(
                        container_port=<NAME>_HTTP_PORT,
                        host_port=<NAME>_HTTP_PORT,
                    ),
                ],
                environment=environment,
                secrets=secrets,
            ),
        )
        # Match the namespace of the registry above (ghcr_mirror_namespace if
        # the image came from foundation.ghcr_mirror_base).
        service.grant_pull_through_cache(foundation.dockerhub_mirror_namespace)

        ###
        # ALB + routing
        alb = PublicHttpAlb(
            self,
            "PublicHttpAlb",
            fqdn=fqdn,
            a_record=cfg.subdomain,
            zone=foundation.public_zone,
            vpc=foundation.vpc,
        )
        alb.https_listener.add_targets(
            "Targets",
            port=<NAME>_HTTP_PORT,
            protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[service.service],
            deregistration_delay=Duration.seconds(30),
            health_check=elbv2.HealthCheck(
                path="/health",  # <- replace with the upstream's health path
                healthy_http_codes="200",
            ),
        )

        ###
        # Security group + DB wiring
        service.security_group.add_ingress_rule(
            alb.security_group,
            ec2.Port.tcp(<NAME>_HTTP_PORT),
            "ALB to <Name> HTTP",
        )
        data.database.grant_connect(
            self,
            "<Name>DbIngress",
            peer=service.service,
            description="<Name> to DB",
        )
```

**Variations to crib from real stacks:**

- **Multi-FQDN ALB** (e.g. an admin UI on a second hostname): see `HeadscaleStack`'s
  `additional_fqdns=[...]` and host-header routing rules with `priority=` and
  `conditions=[elbv2.ListenerCondition.host_headers([...])]`.
- **Service discovery between two services in the same stack**: see
  `HeadscaleStack`'s `servicediscovery.PrivateDnsNamespace` + `enable_cloud_map`
  pattern. Then add a security-group ingress rule from one service's SG to the
  other's listening port.
- **Init container that materializes config from a secret onto a shared
  volume**: use `SharedVolumeInit` from `infra/constructs/shared_volume_init.py`.
  See `HeadscaleStack`'s `NoiseKeyInit` and `HeadplaneConfigInit` for AWS-CLI +
  `jq` + `python3` heredoc examples.
- **EFS volume mounted into the task**: see the `efs.FileSystem` + access point
  + `task_defn.add_volume(...)` + `add_mount_points(...)` block in
  `VaultwardenStack`. Don't forget the EFS SG ingress on TCP/2049 from the task
  SG and `filesystem.grant_read_write(service.task_defn.task_role)`.
- **Lambda-managed secret via Custom Resource** (e.g. an admin API key, a
  preauthkey, a one-time bootstrap): see `HeadscaleStack`'s `AdminApiKey` and
  `ExitNodePreauthkey` patterns — `lambda_python.PythonFunction` + `cr.Provider`
  + `CustomResource(properties={"Trigger": "v1"})`. Bump the `Trigger` to force
  a re-run on the next deploy.
- **EC2-backed compute** (kernel features, hostpath volumes, `NET_ADMIN`): see
  `PrivateEgressEc2Service` usage in `HeadscaleStack`'s exit node block.
- **Lightweight HTTP API (Lambda + API Gateway HTTP API)**: see
  `WebFingerStack` — `PublicHttpApi` instead of `PublicHttpAlb`, no Fargate
  service at all.

If you skip the database, drop the `data: DataExports` field from `<Name>Imports`,
the `db_secret` block, the DB env/secrets, and the `data.database.grant_connect`
call. (Pure stateless services like `WebFingerStack` show this minimal shape.)

---

## Step 2 — Create `infra/models/<name>_config.py`

Mirror `VaultwardenConfig` for an "image-version + db + task + smtp" service:

```python
from dataclasses import dataclass
from typing import Any, Self

from .db_config import DbConfig
from .fargate_task_config import FargateTaskConfig
from .smtp_config import SmtpConfig  # only if the stack sends mail


@dataclass(frozen=True)
class <Name>Config:
    subdomain: str
    image_version: str
    db: DbConfig
    task: FargateTaskConfig
    smtp: SmtpConfig  # drop if not needed

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            subdomain=data["subdomain"],
            image_version=data["image_version"],
            db=DbConfig.load(data["db"]),
            task=FargateTaskConfig.load(data["task"]),
            smtp=SmtpConfig.load(data["smtp"]),
        )
```

Patterns to reuse from sibling configs:

- Multiple Fargate services in one stack (server/worker, headscale/headplane):
  add one `FargateTaskConfig` field per service — see `AuthentikConfig`
  (`server`, `worker`) and `HeadscaleConfig` (`headscale`, `headplane`).
- Nested config-only dataclass (e.g. `AuthentikUserConfig`,
  `ExitNodeConfig`): define it in the same module above the parent and `load`
  it inline. Only split into its own model file when reused across stacks
  (`DbConfig`, `FargateTaskConfig`, `SmtpConfig`, `DbInstanceConfig`).
- Lists from TOML: cast with `list(data["dns_nameservers"])` — see
  `HeadscaleConfig`.
- Optional fields with defaults: use `data.get("port", 587)` — see
  `SmtpConfig`.

---

## Step 3 (optional) — Create `infra/models/<name>_exports.py`

Skip this unless another stack needs to reference resources from the new one.
Producer pattern (`DataExports`, `FoundationExports`):

```python
from dataclasses import dataclass

from aws_cdk import aws_ecs as ecs


@dataclass(frozen=True)
class <Name>Exports:
    cluster: ecs.ICluster
    # ... whatever else downstream stacks need
```

In the stack, populate `self.exports = <Name>Exports(...)` at the end of
`__init__` (see `FoundationStack` and `DataStack`). In `app.py`, capture the
return value: `<name> = <Name>Stack(...).exports`.

---

## Step 4 — Wire `app.py` (via `infra/app_builder.py`)

`app.py` itself doesn't change — it just calls `build_app`. Edit
`infra/app_builder.py`:

1. Add the import beside the existing stack imports:
   ```python
   from .stacks.<name>_stack import <Name>Imports, <Name>Stack
   ```
2. Add `<Name>Stack(...)` after the existing stacks. For a typical
   foundation+data consumer:
   ```python
   <Name>Stack(
       app,
       "<Name>Stack",
       imports=<Name>Imports(
           cfg=cfg.<name>,
           foundation=foundation,
           data=data,
           # authentik_issuer_base=authentik_issuer_base,  # if SSO
       ),
       env=env,
   )
   ```
3. **If the stack has a Postgres database**, append `cfg.<name>.db` to the
   `databases=[...]` list passed to `DataImports`. Without this, the
   `DataStack` Lambda won't create the logical DB and the new service will
   fail to connect on first deploy.

`AppConfig` in `infra/models/app_config.py` also needs the new field:

```python
from .<name>_config import <Name>Config

@dataclass(frozen=True)
class AppConfig:
    foundation: FoundationConfig
    data: DataConfig
    # ...
    <name>: <Name>Config

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            foundation=FoundationConfig.load(data["foundation"]),
            # ...
            <name>=<Name>Config.load(data["<name>"]),
        )
```

---

## Step 5 — Add a `[<name>]` block to `config.toml`

Copy the shape that matches the new config dataclass. Snake_case keys, nested
TOML tables for sub-configs (`[<name>.db]`, `[<name>.task]`, `[<name>.smtp]`).
Example for the Vaultwarden-shaped template:

```toml
[<name>]
subdomain = "<name>"
image_version = "1.2.3"

[<name>.db]
name = "<name>"
secret_name = "<name>/database"

[<name>.task]
cpu = 512
memory_limit_mib = 1024
desired_count = 1
min_healthy_percent = 100

[<name>.smtp]
host = "smtp.chiiiirs.com"
from_email_address = "<name>@chiiiirs.com"
```

Keep `desired_count = 1` and `min_healthy_percent = 100` for a single-task
service (matches every existing service stack in this repo).

---

## Step 6 — Bootstrap the secrets

Append the new bootstrap commands to the **Manual Bootstrapping** section of
`README.md` so the next operator (or you, six months from now) can find them.
The ordering and format must match the existing list:

```sh
bin/aws-write-secret <name>/database --template='{"username":"<name>"}' --key=password --length=32 --exclude-punctuation
```

Add one `bin/aws-write-secret` line per secret the stack reads. Patterns:

- **Single-value secret** (admin token, cookie key): wrap in
  `{"secret":"..."}` so the ECS `name-??????` IAM grant pattern matches when
  resolved by name.
  ```sh
  bin/aws-write-secret <name>/admin-token --template='{}' --key=secret --length=64 --exclude-punctuation
  ```
- **Random bytes** (noise key, cookie secret):
  ```sh
  bin/aws-write-secret <name>/noise-private-key --template='{}' --key=secret --bytes=32
  ```
- **Username + password** (DB credential, SMTP login):
  ```sh
  bin/aws-write-secret <name>/database --template='{"username":"<name>"}' --key=password --length=32 --exclude-punctuation
  bin/aws-write-secret <name>/smtp --template='{"username":"USERNAME"}' --key=password
  ```
- **OIDC client** (issued by Authentik blueprint, manually seeded once):
  ```sh
  bin/aws-write-secret authentik/oidc/<name> -
  # paste: {"client_id":"...","client_secret":"..."}
  ```
- **Lambda-managed placeholder** (rotated by the stack on first deploy, like
  `headscale/admin-api-key`):
  ```sh
  echo -n pending | bin/aws-write-secret <name>/<thing> --template='{}' --key=secret -
  ```

Then run each command against the operator's AWS account. See the README for
the full canonical list as a reference.

---

## Step 7 — Tests

`tests/test_synth.py` runs `build_app` once and asserts each stack's template
synthesizes. Add one method following the existing pattern:

```python
def test_<name>_stack(self) -> None:
    self.assertIn("Resources", self._template("<Name>Stack"))
```

`tests/test_pyright.py` and `tests/test_validators.py` pick up the new files
automatically — no edits needed.

---

## Step 8 — Validate, test, synth

Run these in order. Don't deploy until all three pass.

```bash
bin/validate                          # black + pyright on the changed files
bin/test                              # runs test_pyright, test_synth, test_validators
bin/cdk synth <Name>Stack             # confirm CloudFormation emits cleanly
```

If `bin/cdk synth` succeeds, the stack is ready to deploy:

```bash
bin/cdk deploy <Name>Stack --trace --require-approval never
```

For multi-stack deploys (e.g. when this stack adds a new logical DB and
`DataStack` needs to re-run its init Lambda first), include `DataStack` in the
deploy list so the dependency edges run in the right order:

```bash
bin/cdk deploy DataStack <Name>Stack --trace --require-approval never
```
