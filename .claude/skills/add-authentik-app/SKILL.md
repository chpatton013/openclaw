---
name: add-authentik-app
description: Wire a new OIDC application into Authentik end-to-end — author the blueprint, bootstrap the Secrets Manager entry, plumb client_id/client_secret into the consumer ECS service, deploy in the right order, and validate. Use whenever a new downstream service needs SSO via Authentik (e.g. Vaultwarden, future Matrix Synapse, etc.).
allowed-tools: Bash
---

# Add an Authentik OIDC application

App nickname: **$ARGUMENTS**

If `$ARGUMENTS` is empty, ask the user for the nickname (e.g. `vaultwarden`,
`matrix`) before proceeding. The nickname is used verbatim as:

- The blueprint filename: `assets/authentik/blueprints/<name>.yaml`
- The application slug, provider name, and group name inside the blueprint
- The Secrets Manager entry: `authentik/oidc/<name>`
- The `AK_BP_<NAME>_*` env-var prefix that Authentik reads when seeding the blueprint

The running example below uses **vaultwarden** because it's the next planned
SSO integration (see README TODO: "Vaultwarden — Enable SSO").

---

## Step 1 — Author the blueprint

Create `assets/authentik/blueprints/<name>.yaml`. The minimal shape — copied
from `headscale.yaml` / `headplane.yaml` / `tailscale.yaml`, all of which are
worth glancing at — is four entries: a group, an OAuth2 provider, an
application, and a policy binding that gates the app on group membership.

```yaml
version: 1
metadata:
  name: Vaultwarden OIDC application
entries:
  - model: authentik_core.group
    identifiers:
      name: vaultwarden
    attrs:
      users:
        - !Find [authentik_core.user, [username, !Env AK_BP_USER_USERNAME]]
  - model: authentik_providers_oauth2.oauth2provider
    id: vaultwarden-provider
    identifiers:
      name: vaultwarden
    attrs:
      client_type: confidential
      client_id: !Env AK_BP_VAULTWARDEN_CLIENT_ID
      client_secret: !Env AK_BP_VAULTWARDEN_CLIENT_SECRET
      redirect_uris:
        - matching_mode: strict
          url: !Env AK_BP_VAULTWARDEN_REDIRECT_URI
      authorization_flow: !Find [authentik_flows.flow, [slug, default-provider-authorization-implicit-consent]]
      invalidation_flow: !Find [authentik_flows.flow, [slug, default-provider-invalidation-flow]]
      property_mappings:
        - !Find [authentik_providers_oauth2.scopemapping, [scope_name, openid]]
        - !Find [authentik_providers_oauth2.scopemapping, [scope_name, profile]]
        - !Find [authentik_providers_oauth2.scopemapping, [scope_name, email]]
      signing_key: !Find [authentik_crypto.certificatekeypair, [name, authentik Self-signed Certificate]]
  - model: authentik_core.application
    identifiers:
      slug: vaultwarden
    attrs:
      name: Vaultwarden
      provider: !KeyOf vaultwarden-provider
      policy_engine_mode: any
      open_in_new_tab: true
      # Optional: meta_launch_url: !Env AK_BP_VAULTWARDEN_LAUNCH_URL
  - model: authentik_policies.policybinding
    identifiers:
      target: !Find [authentik_core.application, [slug, vaultwarden]]
      group: !Find [authentik_core.group, [name, vaultwarden]]
    attrs:
      order: 0
```

Notes on the placeholders:

- `AK_BP_USER_USERNAME` is the primary user (defined in `user.yaml`) — every
  app's group includes them by default. Plumbed into the Authentik task by
  `infra/stacks/authentik_stack.py` from `cfg.user.username`.
- `AK_BP_<NAME>_CLIENT_ID` / `..._CLIENT_SECRET` come from the
  `authentik/oidc/<name>` secret (Step 2) via `ecs.Secret.from_secrets_manager`
  in `authentik_stack.py` (Step 3 will add these env entries).
- `AK_BP_<NAME>_REDIRECT_URI` is a plain env var (not a secret) supplied via
  `AuthentikImports` — see `infra/app_builder.py` for the pattern.
- Add `meta_launch_url` only if the app needs a non-trivial launch path
  (Headplane uses `/admin`); omit for apps where the bare FQDN works.

---

## Step 2 — Bootstrap the Secrets Manager entry

Create `authentik/oidc/<name>` holding `client_id` and `client_secret`. The
README's "Manual Bootstrapping" section uses the same pattern for the
existing apps — a JSON blob with both keys:

```sh
bin/aws-write-secret authentik/oidc/vaultwarden -
# stdin then expects:
# {"client_id":"vaultwarden","client_secret":"<random-32-or-more-chars>"}
```

**On the first deploy** the blueprint is the source of truth: it `!Env`-reads
`AK_BP_VAULTWARDEN_CLIENT_ID` / `AK_BP_VAULTWARDEN_CLIENT_SECRET` and creates
the Authentik provider with exactly those values. So whatever you put in the
secret now is what the OIDC provider will be configured with — pick real,
secure values up front rather than placeholders. `client_id` is conventionally
the app slug; `client_secret` should be a freshly generated random string
(e.g. `openssl rand -hex 32`).

This is unlike Lambda-managed secrets (`headscale/admin-api-key`,
`headscale/exit-node/preauthkey`) where a `pending` placeholder is rotated
in-place by a Custom Resource. Authentik blueprints don't rotate — they seed.

Verify after writing:

```sh
bin/aws secretsmanager get-secret-value \
  --secret-id authentik/oidc/vaultwarden \
  --query SecretString --output text | jq '.client_id, (.client_secret | length)'
```

---

## Step 3 — Plumb the secret into the consumer stack

The canonical example is `infra/stacks/headscale_stack.py` (look at
`headscale_oidc_secret` and `HEADSCALE_OIDC_CLIENT_ID` /
`HEADSCALE_OIDC_CLIENT_SECRET`). Three pieces:

### 3a. Reference the secret in the consumer stack

```python
oidc_secret = secretsmanager.Secret.from_secret_name_v2(
    self, "OidcSecret", "authentik/oidc/vaultwarden"
)
```

### 3b. Wire the client_id/client_secret into the container

Use `ecs.Secret.from_secrets_manager(secret, "client_id")` so ECS resolves the
field-ref form (`name:client_id::`) — that's the form whose IAM check passes
against CDK's `name-??????` wildcard grant. See the README "Secrets Format
Convention" for why.

```python
secrets = {
    # ...existing secrets...
    "OIDC_CLIENT_ID": ecs.Secret.from_secrets_manager(oidc_secret, "client_id"),
    "OIDC_CLIENT_SECRET": ecs.Secret.from_secrets_manager(oidc_secret, "client_secret"),
}
```

The exact env-var names are app-specific. Vaultwarden expects `SSO_CLIENT_ID`
/ `SSO_CLIENT_SECRET` plus `SSO_AUTHORITY` (issuer) and `SSO_ENABLED=true`.
Headscale uses `HEADSCALE_OIDC_CLIENT_ID`. Read the upstream image's docs.

### 3c. Set the OIDC issuer env var

The issuer URL is derived from the app slug in the blueprint. Authentik's
default OIDC issuer for an application with slug `vaultwarden` is:

```python
issuer = f"{authentik_issuer_base}/application/o/vaultwarden/"
```

`authentik_issuer_base` is already plumbed into `HeadscaleImports` — add the
same field to your stack's imports dataclass and forward it from
`infra/app_builder.py`.

### 3d. (Authentik blueprint env) — add redirect URI to AuthentikImports

If you didn't reuse an existing redirect-URI env, extend
`AuthentikImports` in `infra/stacks/authentik_stack.py` (alongside
`tailscale_redirect_uri`, `headscale_redirect_uri`, etc.) and forward the
value from `infra/app_builder.py`:

```python
# app_builder.py
vaultwarden_redirect_uri=f"https://{vaultwarden_fqdn}/identity/connect/oidc-signin",
```

Then in `authentik_stack.py`:

```python
# common_env (plain env)
"AK_BP_VAULTWARDEN_REDIRECT_URI": imports.vaultwarden_redirect_uri,

# common_secrets (from the new secret)
"AK_BP_VAULTWARDEN_CLIENT_ID": ecs.Secret.from_secrets_manager(
    vaultwarden_oidc_secret, "client_id"
),
"AK_BP_VAULTWARDEN_CLIENT_SECRET": ecs.Secret.from_secrets_manager(
    vaultwarden_oidc_secret, "client_secret"
),
```

…and reference the new secret near the top of `AuthentikStack`:

```python
vaultwarden_oidc_secret = secretsmanager.Secret.from_secret_name_v2(
    self, "VaultwardenOidcSecret", "authentik/oidc/vaultwarden"
)
```

The init container in `AuthentikStack` already syncs every blueprint in
`assets/authentik/blueprints/` from S3 (`s3deploy.BucketDeployment` populates
the bucket; `SharedVolumeInit` runs `aws s3 sync` on each task start). No
deployment plumbing change is needed for the blueprint file itself.

---

## Step 4 — Deploy

Order matters because the Authentik tasks must reconcile the new blueprint
*before* the consumer service starts an OIDC handshake against a provider
that doesn't exist yet:

```sh
# 1. AuthentikStack first — uploads the new YAML to S3 and the next
#    server/worker task pair sync it into /blueprints/custom and apply it.
bin/cdk deploy AuthentikStack

# 2. Then the consumer stack.
bin/cdk deploy VaultwardenStack
```

Authentik's worker reconciles blueprints on a periodic schedule (and on
task start). Allow ~1–2 minutes after the AuthentikStack deploy completes
before deploying the consumer; check the worker logs to confirm the
blueprint applied:

```sh
# bin/aws is awscli v1 (no `logs tail`); use describe + get.
LOG_GROUP=$(bin/aws logs describe-log-groups \
  --query 'logGroups[?contains(logGroupName, `AuthentikStack`) && contains(logGroupName, `Worker`)].logGroupName | [0]' \
  --output text)
STREAM=$(bin/aws logs describe-log-streams --log-group-name "$LOG_GROUP" \
  --order-by LastEventTime --descending --max-items 1 \
  --query 'logStreams[0].logStreamName' --output text)
bin/aws logs get-log-events --log-group-name "$LOG_GROUP" \
  --log-stream-name "$STREAM" --limit 200 \
  --query 'events[].message' --output text \
  | tr '\t' '\n' | grep -i "blueprint\|<name>"
```

You're looking for an `applied blueprint` line referencing `<name>.yaml` with
no error. If you see `error applying blueprint` see the pitfalls section.

**SaaS-side step?** Most apps don't need one — the blueprint creates the
provider entirely inside Authentik. Tailscale is the exception (see README
"Post-Deploy Setup": Tailscale's SSO is registered on tailscale.com using
the `client_id` / `client_secret` you wrote in Step 2). For self-hosted apps
like Vaultwarden / Matrix, the consumer container reads the same secret
directly — no SaaS handshake.

---

## Step 5 — Validate

1. **Authentik admin UI** — visit `https://auth.<public_domain>/if/admin/`
   (log in as the primary user from `cfg.user.username`). Confirm:
   - Applications → the new app appears with the right slug.
   - Providers → the OIDC2 provider exists, redirect URI matches Step 3d.
   - Directory → Groups → `<name>` group exists with the primary user as a
     member.

2. **OIDC discovery** — sanity-check the issuer is reachable:

   ```sh
   curl -sf "https://auth.<public_domain>/application/o/<name>/.well-known/openid-configuration" | jq .issuer
   ```

3. **End-to-end login** — open the consumer in a private window, click its
   SSO button, expect a redirect to Authentik, consent, and bounce back
   logged in. Tail the consumer's ECS logs while you do this; an OIDC
   misconfiguration (wrong issuer, bad redirect URI, missing scopes) shows
   up immediately as a 4xx in the callback path.

---

## Common pitfall — group doesn't exist yet

If the blueprint's `policybinding` `!Find`s a group that hasn't been created
yet (e.g. you reordered entries so the binding precedes the group, or you
referenced a group from a *different* blueprint that hasn't been applied),
Authentik returns a 500 from the blueprint apply step. The worker log shows
`Failed to apply blueprint ... Group matching query does not exist`.

Fix: ensure the `authentik_core.group` entry is **above** the
`authentik_policies.policybinding` entry inside the same blueprint file (the
existing blueprints all do this). Cross-blueprint group references are
fragile because there's no explicit ordering — keep each app's group in its
own blueprint.

Other failure modes worth knowing:

- **Redirect URI mismatch**: Authentik's provider has the URI hard-coded to
  whatever `AK_BP_<NAME>_REDIRECT_URI` was at apply time. If the consumer's
  FQDN changes, redeploy AuthentikStack to re-seed; the provider doesn't
  re-read on its own.
- **Stale `client_secret` in Authentik**: editing the value in Secrets
  Manager *after* the blueprint has been applied does **not** propagate —
  Authentik treats the provider's stored secret as authoritative. To rotate,
  edit the value in the Authentik admin UI (Providers → the provider → Edit)
  or delete the provider so the blueprint reseeds it on the next reconcile.
