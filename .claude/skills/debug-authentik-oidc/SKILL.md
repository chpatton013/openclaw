---
name: debug-authentik-oidc
description: Diagnose end-to-end SSO failures when a downstream service (Roundcube, rspamd, Vaultwarden, etc.) talks to Authentik via OIDC. Covers blueprint reapply, redirect_uri mismatches, behind-proxy HTTPS, Roundcube oauth2 plugin gotchas, Dovecot OAUTHBEARER passdb config, and CDK asset build failures that silently leave running tasks on stale code. Use whenever SSO works for one service but breaks for another, or after wiring a new OIDC client where one of the pieces lights up red.
allowed-tools: Bash
---

# Debug an Authentik OIDC integration

This skill is the runbook for "SSO didn't work, where's it breaking?" Lessons
distilled from wiring Roundcube through Authentik via end-to-end OAUTHBEARER
to Dovecot. Most apply to any future service that uses Authentik as IdP.

The flow has more layers than feels reasonable, and each layer has its own
failure mode that looks generic ("login failed", "internal error", "missing
redirect_uri"). Walk down the layers; don't try to guess.

---

## Layer 0 — Did your code actually deploy?

CDK silently skips an asset and reports `UPDATE_COMPLETE` if the asset build
fails. The running ECS task stays on the prior task definition. This is the
most insidious failure mode in the entire chain because everything *looks*
green.

**Check first when something doesn't behave as expected:**

```sh
# 1. Was an asset build skipped?
grep -iE "Failed to build asset|429 Too Many Requests" \
    /private/tmp/claude-503/.../tasks/<deploy-task>.output

# 2. What task definition is the service actually running?
bin/aws ecs describe-services --cluster <cluster> --services <svc> \
    --query 'services[0].taskDefinition' --output text

# 3. List active task def revisions for the family. If you only see :2 but
#    expected :7, your last several "successful" deploys were no-ops.
bin/aws ecs list-task-definitions \
    --family-prefix <Family> --status ACTIVE --sort DESC --max-results 5 \
    --output text

# 4. Compare the deployed env / image to what the synth produces today.
bin/aws ecs describe-task-definition \
    --task-definition <Family>:<rev> \
    --query 'taskDefinition.containerDefinitions[*].{name:name,image:image,env:environment}' \
    --output json
bin/cdk synth <Stack> | grep -A3 <ENV_VAR_OR_IMAGE_TOKEN>
```

If the deployed task is older than your code expects, fix the build failure
(see "Asset build pitfalls" below) and redeploy.

---

## Layer 1 — Authentik blueprint didn't pick up your env-var change

Authentik blueprints reconcile on **YAML file hash**, not on the resolved
`!Env` value. If you only changed an env var that the blueprint reads via
`!Env AK_BP_X_Y`, the YAML body is unchanged → same hash → Authentik
considers the blueprint already applied and skips reconciliation.

**Symptoms:**

- ECS env on the Authentik task shows the new value (`env | grep AK_BP_FOO`).
- Authentik admin UI / `oauth2provider` row in the DB still shows the old
  value.
- OIDC errors that point back at the IdP's stored config (e.g. "missing,
  invalid, or mismatching redirection URI" for a redirect URI that you
  swear you set correctly).

**This codebase auto-handles it.** AuthentikStack hashes every blueprint
YAML body together with every `AK_BP_*` env value via
`_stamp_blueprints()`, then:

1. Stamps each YAML with a `# blueprint-inputs-hash:` comment so its body
   hash differs whenever any input changes — Authentik reapplies on next
   discovery.
2. Sets `AUTHENTIK_BLUEPRINT_SYNC_VERSION` to the same hash so the
   worker's task definition diffs and ECS rolls the worker, which
   re-syncs the stamped YAMLs.

So in normal operation: change the env var or edit a YAML, run
`bin/cdk deploy AuthentikStack`, the change takes effect. No manual bump.

**When this still goes wrong:**

- `_stamp_blueprints()` only hashes the dict you pass it as `env`. If you
  add a new `AK_BP_FOO` to `common_env` but forget to add it to
  `blueprint_env`, FOO-only changes won't flip the hash. Keep the two
  lists in sync — `common_env` includes `**blueprint_env` precisely so
  this stays one place.
- A blueprint references a value that's *not* an `AK_BP_*` env (e.g. a
  hard-coded string in the YAML that you change). The YAML body hash
  changes anyway, so this case works without you touching the env list.
- Manual edits to the BucketDeployment, or pointing it at a different
  source dir, will bypass the stamping. Don't do that.

**Diagnostic — confirm the hash actually flipped:**

```sh
# Compute what the stamping helper would produce now
bin/cdk synth AuthentikStack | grep "blueprint-inputs-hash:" | head -1

# Compare to what the running task has
CLUSTER=...; TASK=<worker-task>
bin/aws ecs execute-command --cluster "$CLUSTER" --task "$TASK" \
    --container Container --interactive \
    --command "bash -c 'tail -1 /blueprints/custom/<your-app>.yaml'"
```

If the synth-time hash differs from the running-task hash, the worker
hasn't rolled — re-run `bin/cdk deploy AuthentikStack` and check the
deploy output for an asset-build skip (Layer 0).

**Verify the worker actually has the new YAML:**

```sh
CLUSTER=<cluster>
SVC=$(bin/aws ecs list-services --cluster "$CLUSTER" \
  --query 'serviceArns[?contains(@, `Worker`)] | [0]' --output text | awk -F/ '{print $NF}')
TASK=$(bin/aws ecs list-tasks --cluster "$CLUSTER" --service-name "$SVC" \
  --query 'taskArns[0]' --output text | awk -F/ '{print $NF}')
bin/aws ecs execute-command --cluster "$CLUSTER" --task "$TASK" --container Container --interactive \
  --command "bash -c 'head -10 /blueprints/custom/<your-app>.yaml'"
```

If the on-disk YAML is the OLD one even though the env says new, the
worker hasn't rolled. If the YAML is new but Authentik's stored config is
old, the worker rolled but didn't reconcile (rare; usually means the hash
trick didn't take — try a more visible change like editing
`metadata.name`).

---

## Layer 2 — Authentik OIDC URL structure (the prefix trap)

Authentik exposes one shared OAuth2 endpoint per category and disambiguates
by `client_id`. The per-app slug is in the **issuer** URL only.

```
authorization_endpoint = https://auth.<domain>/application/o/authorize/   # NOT /<slug>/authorize/
token_endpoint         = https://auth.<domain>/application/o/token/       # NOT /<slug>/token/
userinfo_endpoint      = https://auth.<domain>/application/o/userinfo/    # NOT /<slug>/userinfo/
introspection_endpoint = https://auth.<domain>/application/o/introspect/  # NOT /<slug>/introspect/
jwks_endpoint          = https://auth.<domain>/application/o/<slug>/jwks/ # YES, has slug
issuer                 = https://auth.<domain>/application/o/<slug>/      # YES, has slug
```

Confirm by hitting the discovery endpoint for the app:

```sh
curl -sf https://auth.<domain>/application/o/<slug>/.well-known/openid-configuration \
    | jq '{authorization_endpoint, token_endpoint, userinfo_endpoint, issuer}'
```

If you see "404 Not Found" from Authentik when the OIDC client redirects
to authorize, you're sending it to `<base>/<slug>/authorize/` instead of
`<base>/authorize/`.

---

## Layer 3 — `redirect_uri` mismatch at the IdP

Authentik strict-matches `redirect_uri` against the registered URL — exact
protocol, host, port, path. The two real failure modes:

### 3a. The blueprint registers a different URL than the client sends

In the codebase the blueprint reads `AK_BP_<APP>_REDIRECT_URI`, which flows
from `infra/app_builder.py`. Make sure the value you set there matches what
the client actually computes at runtime.

To capture what the client actually sends, hit its SSO entrypoint with curl
and read the `Location:` redirect:

```sh
curl -sI "https://<service>.<domain>/<sso-entry>" \
    | grep -i 'location:' | tr '&' '\n' | grep -i redirect_uri
```

URL-decode the value and compare against the blueprint's `redirect_uris.url`.

### 3b. The client sends `http://` because it doesn't see TLS

Apache and PHP behind a TLS-terminating ALB only see plain HTTP locally.
PHP frameworks then build URLs like `http://<host>/...`, which the IdP
rejects.

Roundcube specifically doesn't trust `X-Forwarded-Proto` by default.
The fix is a one-time `$_SERVER` shim early in PHP config. We do this in
`oauth.inc.php` (see `assets/webmail_init/init.sh` step 2):

```php
if (($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https') {
    $_SERVER['HTTPS'] = 'on';
    $_SERVER['SERVER_PORT'] = 443;
}
```

For other PHP apps the same pattern works. For non-PHP apps, look for a
config option named `proxy_ssl_trust`, `force_https`, or
`forwarded_for_trust`.

Symptom: blueprint has `https://...`, client sends `http://...`,
Authentik rejects with "missing, invalid, or mismatching redirection URI".

---

## Layer 4 — Roundcube specifics (or "a downstream PHP webmail")

Roundcube's docker entrypoint is opinionated. Things you'll trip on:

### 4a. Don't override the container `command`

The image's `docker-entrypoint.sh` only runs config generation when
`$1 == apache2-foreground` (or `php-fpm` / `bin*`). Setting CDK's
`command=["sh", "-c", "..."]` in the task def replaces `apache2-foreground`,
the entrypoint sees `$1 == "sh"` and skips config gen, and
`/var/www/html/config/config.inc.php` is never created.

Symptom: `Directory nonexistent` when your wrapper tries to write there.

Don't override the container's CMD. Hook in elsewhere (see 4b).

### 4b. Roundcube auto-includes `/var/roundcube/config/*.php`

The Roundcube docker entrypoint has this loop (cite for posterity):

```bash
for fn in `ls /var/roundcube/config/*.php 2>/dev/null || true`; do
    echo "include('$fn');" >> config/config.docker.inc.php
done
```

So if EFS is mounted at `/var/roundcube` and the init container writes
`/var/roundcube/config/oauth.inc.php`, Roundcube's main container picks it
up automatically. **This is the right hook for layered config.** Don't
fight it by templating into `/var/www/html/config/config.inc.php` —
that file is regenerated on every task start.

### 4c. Sqlite first-boot bootstrap

Roundcube's entrypoint runs schema init only if `sqlite.db` is **absent**.
If a previous failed boot left a 0- or 12-byte file on EFS, the entrypoint
skips schema load, and Roundcube serves "no such table: session" forever.

The init container in `assets/webmail_init/init.sh` re-creates the DB if
the `session` table is missing. Idempotent, safe to run on every task
start, preserves accumulated user state when the DB is healthy.

---

## Layer 5 — Dovecot OAUTHBEARER passdb (Dovecot 2.3.x on docker-mailserver 14)

Goal: Dovecot accepts an Authentik-issued access token as IMAP
authentication.

### 5a. Use introspection, not local JWKS validation

Dovecot 2.3.19 (in DMS 14.0.0) does NOT ship the `fs:posix:` dict driver
that the popular "JWKS-on-disk" guides assume. Available dict drivers in
that version are `file:`, `proxy:`, `redis:`, `sql:`, `cdb:`. None of them
have a clean per-kid file layout.

If you try `local_validation_key_dict = fs:posix:prefix=...`, Dovecot logs
`Local validation failed: RS256 key '<kid>' not found` for every login —
not because the file is missing, but because the dict driver isn't valid
and the lookup returns nothing.

**Use introspection instead.** One HTTPS round-trip per IMAP login to
Authentik. Slower but reliable on this image:

```ini
# dovecot-oauth2.conf.ext
introspection_url = https://auth.<domain>/application/o/introspect/
introspection_mode = post
client_id     = <oidc client_id>
client_secret = <oidc client_secret>
issuers       = https://auth.<domain>/application/o/<slug>/
username_attribute = email
username_format    = %Lu
active_attribute   = active
active_value       = true
```

The `client_id`/`client_secret` are the same Authentik OIDC client values
your downstream service uses. Init container reads them from
`authentik/oidc/<slug>` at task start and templates them into the conf
file on EFS.

### 5b. dovecot.cf to register the passdb (additive)

```
auth_mechanisms = $auth_mechanisms oauthbearer xoauth2
passdb {
  driver = oauth2
  mechanisms = oauthbearer xoauth2
  args = /tmp/docker-mailserver/dovecot-oauth2.conf.ext
}
```

Note `$auth_mechanisms` — single-quoted so the literal Dovecot variable
survives unmangled. This is **additive** to the existing `passwd-file`
passdb, so password-auth clients (mutt, Apple Mail, Thunderbird) keep
working alongside OAUTHBEARER from the webmail.

### 5c. Inspecting failures

Mail container's `/var/log/mail.log` shows the real auth attempts:

```sh
CLUSTER=<cluster>; TASK=<mail-task>
bin/aws ecs execute-command --cluster "$CLUSTER" --task "$TASK" \
    --container Container --interactive \
    --command "bash -c 'grep -iE \"oauth2|XOAUTH\" /var/log/mail.log | tail -10'"
```

Common errors:
- `Local validation failed: RS256 key '<kid>' not found` → using local
  validation; switch to introspection (5a).
- `oauth2 failed: token validation: invalid token` → Authentik introspection
  said `active=false`. Token expired or revoked. Check Authentik token
  TTL (we set `access_token_validity: minutes=10`).
- `oauth2 failed: token validation: issuer mismatch` → `issuers` in
  conf.ext doesn't match `iss` claim. Note the trailing slash — Authentik
  emits `/application/o/<slug>/` with trailing slash.

---

## Layer 6 — CDK asset build pitfalls

### 6a. Don't FROM Docker Hub for a `DockerImageAsset`

CDK's `docker build` runs locally against the public registry. Heavy or
frequently-rebuilt images blow through the unauthenticated rate limit
("429 Too Many Requests"), and CDK skips the asset → CFN sees no diff →
running task stays on stale code (Layer 0).

Use a base image that isn't on Docker Hub when you can:
- `alpine:3.x` is cached almost everywhere; rate limits rarely bite.
- `public.ecr.aws/...` for AWS-published images (if available).
- For "I just need files from upstream", `curl` from GitHub raw at build
  time instead of `FROM` the upstream image — see
  `assets/webmail_init/Dockerfile` for the pattern (alpine + curl-fetched
  Roundcube SQL schema).

### 6b. Build args from CDK config

To keep Dockerfile tags in sync with config.toml versions, pass via
`build_args`:

```python
ecr_assets.DockerImageAsset(
    self, "FooInitImage",
    directory=str(assets.docker_path("foo_init")),
    build_args={"FOO_VERSION": cfg.image_version},
    platform=ecr_assets.Platform.LINUX_AMD64,
)
```

```dockerfile
ARG FOO_VERSION=1.0.0
FROM something:${FOO_VERSION}
```

Watch for tag/source-version drift: `roundcube/roundcubemail:1.6.10-apache`
is the docker tag, but the matching GitHub source tag is `1.6.10` (no
`-apache` suffix). Strip suffixes before passing to a GitHub URL.

---

## Layer 7 — Behind-proxy considerations checklist

If the service sits behind an ALB doing TLS termination:

- [ ] PHP / app trusts `X-Forwarded-Proto` for URL construction (Layer 3b).
- [ ] OIDC redirect URI in the blueprint uses `https://`, not `http://`.
- [ ] App's "advertised hostname" config (e.g.
      `ROUNDCUBEMAIL_REQUEST_PATH`, Vaultwarden's `DOMAIN`) is the public
      URL, not the internal port.
- [ ] ALB target group health check tolerates the slow first-boot of
      multi-daemon containers; set
      `health_check_grace_period=Duration.minutes(3)` on the FargateService
      and bump `unhealthy_threshold_count` to 5 if you need 150s of
      slack (see how MailStack does it for the rspamd UI target group).

---

## Diagnostic command cheat sheet

```sh
# Find the running task for a service
CLUSTER=$(bin/aws ecs list-clusters --query 'clusterArns[0]' --output text | awk -F/ '{print $NF}')
SVC=$(bin/aws ecs list-services --cluster "$CLUSTER" \
    --query 'serviceArns[?contains(@, `<NamePrefix>`)] | [0]' \
    --output text | awk -F/ '{print $NF}')
TASK=$(bin/aws ecs list-tasks --cluster "$CLUSTER" --service-name "$SVC" \
    --query 'taskArns[0]' --output text | awk -F/ '{print $NF}')

# Tail container logs
LG=$(bin/aws logs describe-log-groups \
    --query 'logGroups[?contains(logGroupName, `<Stack>-ServiceLogGroup`)].logGroupName | [0]' \
    --output text)
S=$(bin/aws logs describe-log-streams --log-group-name "$LG" \
    --order-by LastEventTime --descending --max-items 1 \
    --query 'logStreams[0].logStreamName' --output text)
bin/aws logs get-log-events --log-group-name "$LG" --log-stream-name "$S" \
    --limit 200 --query 'events[*].message' --output text | tr '\t' '\n' | tail -50

# ECS exec into a running container
bin/aws ecs execute-command --cluster "$CLUSTER" --task "$TASK" \
    --container Container --interactive \
    --command "bash -c '<your-command>'"

# Authentik OIDC discovery
curl -sf https://auth.<domain>/application/o/<slug>/.well-known/openid-configuration \
    | jq '{authorization_endpoint,token_endpoint,userinfo_endpoint,issuer}'

# What redirect_uri does the client send?
curl -sI "https://<service>.<domain>/<sso-entry>" \
    | grep -i 'location:' | tr '&' '\n' | grep -i redirect_uri
```

---

## When to use this skill

- "I changed the blueprint env var and Authentik still rejects the redirect."
- "OIDC works for service A but service B fails in the IMAP-login step."
- "I deployed and CFN says UPDATE_COMPLETE but the running task behaves like
   the old code."
- "Roundcube login screen says 'Login failed' with no useful detail."
- Wiring a new service to OAUTHBEARER against Dovecot — start at Layer 5.
- Wiring a new OIDC client through ALB-OIDC vs in-app OIDC — Layer 4a is a
   trap-spotter.
