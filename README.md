# Personal Cloud Deployment

This repo hosts the infrastructure-as-code (IaC) for my personal cloud
deployment, which is split across AWS (in progress) and a private homelab
(coming soon).

## Dependencies

- [Node.js and npm](https://docs.npmjs.com/downloading-and-installing-node-js-and-npm)
- [Amazon CDK](https://docs.aws.amazon.com/cdk/v2/guide/getting-started.html)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

## Organization

CDK organizes infrastructure into "stacks". Each stack has its own name, and can
be deployed individually. Each stack is composed of one or more "constructs",
which are either individual infrastructure resources, or collections of
infrastructure resources. The relationships between a parent stack to its child
constructs, and parent constructs to their child constructs creates a resource
dependency tree. Constructs may declare relationships to other constructs across
branches of that tree, or even into the trees of other stacks, forming a rich
DAG. But they can never declare a cyclical dependency.

## Bootstrapping

A few steps need to be followed before we can deploy our infrastructure. The
CDK stacks reference these secrets by name at deploy time, so bootstrapping is
a prerequisite to `cdk deploy` — not an optional convenience.

You can perform each of these steps manually (either through the AWS console, or
using the convenience script), or run the bootstrap script:
- `bin/bootstrap`

Bootstrapping steps:

1. Create a public hosted zone for your domain in AWS.
    - AWS console
        - Route 53 > Hosted Zones > Create hosted zone
        - Fill in your domain, select "Public hosted zone", then "Create hosted zone"
    - Helper script
        - `bin/aws-create-hosted-zone DOMAIN`
2. Create persistent secrets used by AWS services.
    - AWS console
        - TODO
    - Helper script
        - `bin/aws-write-secret authentik/secret-key --length=50 --exclude-punctuation`
        - `bin/aws-write-secret authentik/bootstrap --template='{"email":"EMAIL"}' --key=password`
        - `bin/aws-write-secret authentik/database --template='{"username":"USERNAME"}' --key=password`
        - `bin/aws-write-secret authentik/smtp --template='{"username":"USERNAME"}' --key=password`

## Deploying

AWS:
```
cdk deploy STACK_NAME
cdk deploy --all
```

### Other useful commands

 * `cdk ls`          list all stacks in the app
 * `cdk synth`       emits the synthesized CloudFormation template
 * `cdk deploy`      deploy this stack to your default AWS account/region
 * `cdk diff`        compare deployed stack with current state
 * `cdk docs`        open CDK documentation

## Development

Run validators against the whole repo (or a file/dir/glob subset):

```
bin/validate              # all tracked files
bin/validate --fix        # apply fixers then check
bin/validate infra/       # restrict to a subtree
bin/validate --dirty      # only staged files
```

Install the git pre-commit hook (symlinks `bin/pre-commit` into `.git/hooks/`):

```
bin/pre-commit --install
```

Per-directory validator scoping is driven by `.validator.toml` files. The
repo-root file enables `python-black` and `python-pyright` on all `.py` files;
nested `.validator.toml` files can narrow or extend that config.

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

## Validators

- pyupgrade
- shellcheck
- xml (xml.etree.cElementTree.parse)

- shfmt
- rustfmt
- toml
- yaml
- json
- terraform (terraform fmt)

- gitleaks
