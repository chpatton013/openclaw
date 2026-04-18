# Personal Cloud Deployment

[![test](https://github.com/chpatton013/openclaw/actions/workflows/test.yml/badge.svg)](https://github.com/chpatton013/openclaw/actions/workflows/test.yml)

This repo hosts the infrastructure-as-code (IaC) for my personal cloud
deployment, which is split across AWS (in progress) and a private homelab
(coming soon).

## Getting Started

**Do not skip any of these steps!**

### Write your `config.toml`

Update [`config.toml`](./config.toml) based on your domain and hosting needs.

`foundation.root_domain` may be the only value that **has** to change, but your
hosting needs may dictate changes to other fields. Look at the
[Contents](#Contents) section for a full breakdown of what services you're
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
    --concurrency="$(nproc --all)" \
    --asset-build-concurrency="$(nproc --all)"
```

You can modify those concurrency parameters or replace `--all` with the names of
specific stacks, as needed.

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
        - `bin/aws-write-secret authentik/secret-key --length=50 --exclude-punctuation`
        - `bin/aws-write-secret authentik/bootstrap --template='{"email":"EMAIL"}' --key=password`
        - `bin/aws-write-secret authentik/database --template='{"username":"USERNAME"}' --key=password`
        - `bin/aws-write-secret authentik/smtp --template='{"username":"USERNAME"}' --key=password`

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
    - TODO:
        - Setup an ECR to mirror Authentik container image
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
- Planned stacks:
    - WebFinger
    - Vaultwarden
    - Headscale
    - searXNG
    - Matrix
