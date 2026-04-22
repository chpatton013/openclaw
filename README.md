# Personal Cloud Deployment

[![test](https://github.com/chpatton013/openclaw/actions/workflows/test.yml/badge.svg)](https://github.com/chpatton013/openclaw/actions/workflows/test.yml)

This repo hosts the infrastructure-as-code (IaC) for my personal cloud
deployment, which is split across AWS (in progress) and a private homelab
(coming soon).

## Getting Started

**Do not skip any of these steps!**

### Write your `config.toml`

Update [`config.toml`](./config.toml) based on your domain and hosting needs.

`foundation.public_domain` and `foundation.private_domain` are the values most
likely to change for a fresh deployment. `public_domain` hosts the
control-plane URLs (Authentik, WebFinger, etc.); `private_domain` is reserved
for services that don't need to be reachable from the public internet. Look at
the [Contents](#Contents) section for a full breakdown of what services you're
configuring.

### Setup your AWS credentials

Set up your `default` profile in `~/.aws/config` and `~/.aws/credentials` 

We use the `boto3` and `aws_cdk` Python packages to do all of our AWS API
interaction. Those packages should both use the standard AWS config and
environment variable conventions for configuration and secrets injection, so if
you're familiar with those, you can do something fancier than using the
`default` profile.

### Run one-time bootstrap

Run `bin/bootstrap` to complete all one-time setup steps.

This script will:
1. ensure that `dotslash` is installed on your system (which we use extensively
   to fetch all our dependency tools), and
2. interactively setup the persistent AWS resources that will be referenced by
   the deploy process.

You can perform these bootstrapping steps manually if you want more control, or
need to rerun only a subset of them for some reason.
See the [Manual Bootstrapping](#Manual-Bootstrapping) section below for more
details.

### Run tests

Run `bin/test` to run all tests in the repository.

These tests will, among other things, ensure that your config is setup
correctly, and there are no obvious errors with the CDK IaC before you deploy.

### Deploy

Run this command to deploy the AWS infrastructure:

```sh
bin/cdk deploy --all --trace \
    --require-approval never \
    --concurrency="$(nproc --all)" \
    --asset-build-concurrency="$(nproc --all)"
```

You can modify those concurrency parameters or replace `--all` with the names of
specific stacks, as needed. `--require-approval` can be omitted if you aren't
running with any concurrency.

## Post-Deploy Setup

The Tailscale, Headscale, and Headplane OIDC applications are provisioned
automatically by Authentik blueprints in
[`assets/authentik/blueprints/`](./assets/authentik/blueprints/), synced to
each Authentik task from S3 by an init container on every deploy. The only
remaining manual steps are user/group membership and the Tailscale SaaS-side
registration.

- Authentik
    - Directory > Users > New Service Account
        - Set name to `tailscale`
        - Enable "Create Group" and "Expiring"
        - Create Service Account
        - Copy the password for later
    - Directory > Groups > `tailscale` > Users > Add existing user
        - Select `akadmin` and the `tailscale` service account
        - Confirm and Assign
- Tailscale (SaaS-side)
    - Create a new account with OIDC provider
        - Email address: tailscale@example.com
        - WebFinger URL (automatically populated to https://example.com/.well-known/webfinger)
        - Which identity provider: Authentik
        - Get OIDC issuer
        - Client ID / Client Secret: use the values stored in the
          `authentik/oidc/tailscale` Secrets Manager entry
        - Prompts: consent
        - Sign up with OIDC
    - Authentik redirecting to Tailscale
        - Continue
- Domain registrars
    - After the hosted zone are created, copy the 4 NS records from Route53 into
    the registrar DNS config.

## Manual Bootstrapping

If you don't want to use the automated bootstrapping script, you can perform
each of its steps yourself. To do so, you can either run the associated helper
script for each step, or take matters into your own hands.

1. Install `dotslash` on your system.
    - Manual install
        - [instructions](https://dotslash-cli.com/docs/installation/)
    - Helper script
        - `bash scripts/bootstrap/dotslash.sh`
2. Create a public hosted zone for your domain in AWS.
    - AWS console
        - Route 53 > Hosted Zones > Create hosted zone
        - Fill in your domain, select "Public hosted zone", then "Create hosted zone"
    - Helper script
        - `bin/aws-create-hosted-zone DOMAIN`
3. Create persistent secrets used by AWS services.
    - AWS console
        - TODO
    - Helper script
        - `bin/aws-write-secret ecr-pullthroughcache/ghcr --template='{"username":"GITHUB_USERNAME"}' --key=accessToken  # GitHub PAT with read:packages scope`
        - `bin/aws-write-secret ecr-pullthroughcache/dockerhub --template='{"username":"DOCKERHUB_USERNAME"}' --key=accessToken  # Docker Hub PAT`
        - `bin/aws-write-secret authentik/secret-key --length=50 --exclude-punctuation`
        - `bin/aws-write-secret authentik/bootstrap --template='{"email":"EMAIL"}' --key=password`
        - `bin/aws-write-secret data/database --template='{"username":"USERNAME"}' --key=password`
          (RDS master; read by the `DataStack` init Lambda only — no service uses it)
        - `bin/aws-write-secret authentik/database --template='{"username":"authentik"}' --key=password --length=32 --exclude-punctuation`
        - `bin/aws-write-secret headscale/database --template='{"username":"headscale"}' --key=password --length=32 --exclude-punctuation`
        - `bin/aws-write-secret authentik/smtp --template='{"username":"USERNAME"}' --key=password`
        - `bin/aws-write-secret authentik/oidc/tailscale --template='{"client_id":"CLIENT_ID"}' --key=client_secret`
          (values from the `tailscale` provider registered in Authentik)
        - `bin/aws-write-secret authentik/oidc/headscale -`
          (JSON blob: `{"client_id":"...","client_secret":"..."}` — blueprint
          seeds both into Authentik on first apply)
        - `bin/aws-write-secret authentik/oidc/headplane -`
          (same shape as headscale)
        - `bin/aws-write-secret headscale/noise-private-key --bytes=32`
        - `bin/aws-write-secret headplane/cookie-secret --bytes=32`
        - `bin/aws-write-secret headscale/admin-api-key -  # empty placeholder; populated by HeadscaleStack`
        - `bin/aws-write-secret vaultwarden/database --template='{"username":"vaultwarden"}' --key=password --length=32 --exclude-punctuation`
        - `bin/aws-write-secret vaultwarden/admin-token --length=64 --exclude-punctuation`
        - `bin/aws-write-secret vaultwarden/smtp --template='{"username":"USERNAME"}' --key=password`

## Development

### Pre-commit hook

Install the git pre-commit hook (symlinks `bin/pre-commit` into `.git/hooks/`):

```sh
bin/pre-commit --install
```

### Validators

Run validators against the whole repo (or a file/dir/glob subset):

```sh
bin/validate              # all tracked files
bin/validate --fix        # apply fixers then check
bin/validate infra/       # restrict to a subtree
bin/validate --dirty      # only staged files
```

Per-directory validator scoping is driven by `.validator.toml` files. The
repo-root file enables `python-black` and `python-pyright` on all `.py` files;
nested `.validator.toml` files can narrow or extend that config.

### Tests

Run tests against the whole repo:

```sh
bin/test
```

### Other useful commands

```sh
bin/cdk ls          # list all stacks in the app
bin/cdk synth       # emits the synthesized CloudFormation template
bin/cdk deploy      # deploy this stack to your default AWS account/region
bin/cdk diff        # compare deployed stack with current state
bin/cdk docs        # open CDK documentation
```

## Organization

CDK organizes infrastructure into "stacks". Each stack has its own name, and can
be deployed individually. Each stack is composed of one or more "constructs",
which are either individual infrastructure resources, or collections of
infrastructure resources. The relationships between a parent stack to its child
constructs, and parent constructs to their child constructs creates a resource
dependency tree. Constructs may declare relationships to other constructs across
branches of that tree, or even into the trees of other stacks, forming a rich
DAG. But they can never declare a cyclical dependency.

 ## Contents

### AWS

- [Foundation Stack](./infra/stacks/foundation_stack.py)
    - Declares shared resources used by all other stacks.
    - Resources:
        - Hosted Zone and VPC
        - ECS cluster
- [Authentik Stack](./infra/stacks/authentik_stack.py)
    - OIDC identity provider
    - Resources:
        - Storage: S3 media bucket, RDS PostGres database
        - Services: Authentik Server and Worker Fargate service containers
        - Network: publicly-accessible Application Load Balancer
- [Data Stack](./infra/stacks/data_stack.py)
    - Shared Postgres for stateful services (Authentik, Headscale, ...).
    - Resources:
        - RDS PostgreSQL instance in a private-isolated subnet
        - A CDK custom resource (Provider + Lambda using `pg8000`) that
          creates each logical database named by the stack's `databases=[...]`
          list if it doesn't exist yet
- [Headscale Stack](./infra/stacks/headscale_stack.py)
    - Self-hosted Tailscale control server + Headplane admin UI.
    - Resources:
        - Services: `headscale` and `headplane` Fargate services
        - Network: one public ALB serving both `headscale.<public_domain>`
          and `headplane.<public_domain>` via host-header routing; Cloud Map
          private namespace `headscale.local` for Headplane→Headscale
        - Storage: shared Postgres via DataStack
        - Init: noise private key materialized from
          `headscale/noise-private-key` onto a tmpfs volume by an init
          container before Headscale starts
        - Custom resource: populates `headscale/admin-api-key` by running a
          one-shot Fargate task (`headscale apikeys create`) the first time
          the stack is deployed
    - MagicDNS base domain: `{headscale.private_subdomain}.{foundation.private_domain}`
      (e.g. `ts.example.net`)
- [OpenClaw Stack](./infra/stacks/openclaw_stack.py)
    - Agentic assistant platform
    - Resources:
        - Storage: EFS volume, backup plan
        - Services: EC2 instance running openclaw node daemon
        - Network: VPC
    - Notes:
        - I consider this service to be high-risk to run, so I've isolated it in
          several ways. It has its own VPC, is running on a machine that can
          only be accessed via SSM connection sessions, and currently has no
          privileges to communicate with anything internal.
        - I may want to modify this setup to reuse the foundation VPC and host
          in Fargate. Will need to get more trust in the system first.
- [Vaultwarden Stack](./infra/stacks/vaultwarden_stack.py)
    - Self-hosted Bitwarden-compatible password vault
    - Resources:
        - Storage: EFS `/data`, shared Postgres via DataStack
        - Services: single Fargate task (`vaultwarden/server`) pulled from
          the Docker Hub pull-through cache
        - Network: public ALB at `vaultwarden.<public_domain>`
    - TODO:
        - Wire up Authentik SSO (add `vaultwarden.yaml` blueprint,
          `authentik/oidc/vaultwarden` secret, `SSO_ENABLED` env)
- Planned AWS stacks:
    - searXNG
    - Matrix Synapse
    - mail
- Planned homelab hosting:
    - ownCloud / NextCloud
    - Gitea or Forgejo

- TODO:
    - Automated credential rotation. Two viable paths:
        - **Secrets Manager hosted rotation**
          (`secret.add_rotation_schedule(...,
          hosted_rotation=secretsmanager.HostedRotation.postgres_single_user(
          vpc=foundation.vpc), automatically_after=Duration.days(30))`). AWS-managed
          Lambda rotates the password in Postgres and writes the new value
          back to the secret. Downside: services that read secrets at task
          boot (all ECS tasks here) will keep the old password until redeploy
          — wire an EventBridge rule on the rotation event to
          `ecs:UpdateService --force-new-deployment` so tasks get recycled.
          `multi_user` rotation avoids the restart but requires app-side
          retry-on-auth-fail.
        - **IAM DB auth**. Enable `iam_database_authentication` on the RDS
          instance, grant task roles `rds-db:connect` on the specific DB
          user ARN. Services request a 15-minute token from RDS at connect
          time. No rotation needed because there's no long-lived password.
          Downside: Authentik/Headscale don't natively fetch RDS auth
          tokens — would need a sidecar (`pgbouncer` + token refresh) per
          task. More moving parts.
        - **Recommended order**: per-service users first, then hosted
          rotation with force-new-deployment on rotation events. Defer IAM
          DB auth until there's a real need — the sidecar cost outweighs
          the rotation cost at single-digit-service scale.
    - Vaultwarden
        - Enable SSO
    - Get rid of the Imports types and just use kwargs
    - Be specific about which properties we want from foundation and data exports
    - EFS and RDS backups
