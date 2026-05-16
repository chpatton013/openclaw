---
name: rename-cdk-resource
description: Migrate a CDK-managed AWS resource whose construct path changed (e.g. a SharedEfsVolume / StandardBackupPlan extraction) so the deployed state lines up with the new logical ID, without losing the underlying data. Uses CDK's `cdk import` flow. Never use `override_logical_id(<hardcoded-hash>)` to "fix" the diff — that bakes deployment-specific state into source code that needs to be portable.
allowed-tools: Bash
---

# Rename a CDK resource without losing data

Resource to migrate: **$ARGUMENTS** (give a `<Stack>:<ConstructPath>`
pair, e.g. `MatrixStack:MatrixFs/FileSystem`).

If `$ARGUMENTS` is empty, ask the operator which resource. Only one
resource per invocation -- if multiple resources need migration in the
same stack (typically an EFS family: file system + access points + etc.),
the file system is the only one that needs `cdk import`. Mount targets,
security groups, ingress rules, access points, backup plans, backup
selections, and IAM roles are recreatable without data loss; let CDK
create them fresh.

## Why this skill exists

CDK derives CFN logical IDs from the construct path. When you extract a
resource into a wrapper construct (e.g. `efs.FileSystem(self, "MatrixFs")`
becomes `SharedEfsVolume(self, "MatrixFs").filesystem`, where the FS now
lives at `MatrixStack/MatrixFs/FileSystem`), the logical ID changes from
`MatrixFs672E241B` to `MatrixFsFileSystemFE64CFAE`.

For a stateless resource, CFN destroy/recreate is fine -- that's the
diff `cdk` shows you. For a stateful resource (EFS file system, RDS
instance, S3 bucket with content) destroy/recreate is data loss.

**Do not paper over the diff by calling
`override_logical_id("MatrixFs672E241B")` in source code.** Per the
repo's CLAUDE.md, hardcoded logical IDs are forbidden -- they're
artifacts of one particular deployment's history and they don't
transfer to a fresh fork.

The correct fix is a one-time operational migration: orphan the AWS
resource from CFN via `RemovalPolicy.RETAIN`, then re-adopt it under
the new logical ID via `cdk import`. The source code stays clean.

## Pre-flight checks

1. **Verify `RemovalPolicy.RETAIN`** is set on the resource in the
   *deployed* CFN template (not the synthed-from-current-source one).
   The deployed DeletionPolicy is what CFN consults at removal time.
   ```
   bin/aws cloudformation get-template --region us-west-2 \
     --stack-name <Stack> --query 'TemplateBody' | \
     jq '.Resources.<OldLogicalId>.DeletionPolicy'
   ```
   Expect `"Retain"`. If you get `"Delete"` or `null`, **stop**. Flip
   it to Retain via direct CFN update before proceeding (the AWS
   console's "Edit template" is fastest for a one-line change). The
   `SharedEfsVolume` construct defaults `removal_policy=RETAIN`, but
   legacy inline EFS resources sometimes used `DESTROY`.

2. **Take an AWS Backup recovery point.** This is the only rollback
   path if `cdk import` fails.
   ```
   bin/aws backup start-backup-job \
     --backup-vault-name <vault> \
     --resource-arn <resource-arn> \
     --iam-role-arn <BackupPlan role ARN from the stack>
   ```
   Wait for `aws backup describe-backup-job --backup-job-id <id>` to
   show `State: COMPLETED`. Don't skip this.

3. **Record the physical ID** of the resource (e.g. `fs-0f075e5f...`).
   You'll feed it to `cdk import` in Phase 3.

4. **Identify dependent code.** Anything in the stack that references
   the resource handle (e.g. `filesystem.file_system_id`,
   `filesystem.grant_read_write(...)`, `efs_sg.add_ingress_rule(...)`,
   the `BackupResource.from_efs_file_system(filesystem)` in the backup
   selection, the ECS volume + mount-point definitions in the
   Fargate / EC2 service code) -- you'll comment all of this out in
   Phase 1.

## Four-phase migration

### Phase 1 -- disown

Edit the stack source so the resource (and everything that references
it) is gone from the synthed template, while leaving the rest of the
stack intact:

- Comment out the construct call (e.g. `SharedEfsVolume(...)`).
- Comment out every code path that uses the resource handle.
- For Fargate services that mount the resource: set
  `desired_count=0` so the service stops trying to start without
  `/data`.
- Comment out any `override_logical_id` calls that pin the
  to-be-migrated resource (the whole point is to get rid of these).

**Do not commit yet.** This is a temporary working state.

### Phase 2 -- deploy the disown

Verify the diff:
```
bin/cdk diff <Stack> --exclusively
```

The resource line should say **`orphan`**, not `destroy`. If you see
`destroy`, your pre-flight `RETAIN` check missed something -- stop and
re-verify before deploying.

```
bin/cdk deploy <Stack>
```

After this completes:
- CFN has removed the resource from its template.
- AWS still owns the physical resource (because of `Retain`).
- All dependent CFN resources that *weren't* `Retain` (mount targets,
  access points, security groups, ingress rules, backup plan,
  backup selection, IAM roles) have been deleted from both CFN and
  AWS.
- The service is hard down -- can't mount EFS.

### Phase 3 -- adopt

Revert the Phase 1 commenting so the construct call and all dependent
code paths are back, **without any `override_logical_id` calls**. The
construct now produces the new natural logical ID (e.g.
`MatrixFs/FileSystem` instead of just `MatrixFs`).

Restore `desired_count` to its original value.

Run:
```
bin/cdk import <Stack>
```

CDK identifies new resources in the synthed template (the
construct's L1s at their new construct paths) and prompts:
```
MatrixStack/MatrixFs/FileSystem (AWS::EFS::FileSystem):
  enter PhysicalResourceId (or '' to create new): fs-0f075e5f6306dce2d
```

- For the file system: **enter the physical ID** from Phase 0.
- For everything else (access points, security groups, mount
  targets, backup plan, backup selection, IAM role, ingress rule):
  **hit Enter** to let CDK create them fresh.

CDK runs an IMPORT changeset that binds the new logical ID to the
existing physical resource.

### Phase 4 -- deploy the rest

```
bin/cdk deploy <Stack>
```

This creates the recreatable dependents (mount targets, access
points, etc.) freshly under the new construct paths. The new mount
targets attach to the imported file system using the same VPC
subnets. The Fargate service restarts and mounts the FS, finding
all its existing data (signing keys, media store, mail, whatever)
intact.

For stacks whose imported resource is cross-stack-exported (e.g.
MailStack's `MailFs` is imported by WebmailStack), the export name
auto-derives from the new logical ID. CDK should pull the consumer
stack into the deploy too; expect a small downstream diff (typically
a TaskDef replacement to rebind the import).

## Verification

- `bin/cdk diff <Stack>` after Phase 4: zero EFS / RDS / S3
  destroy/replace lines for the migrated resource family.
- Service-level health check:
  - For EFS-backed services: `bin/cdk-execute-command <Stack>` or
    SSM into the host and verify the expected files are present at
    the mount path.
  - For HTTPS-fronted services: `curl
    https://<service-fqdn>/<health-path>` returns the expected
    response.
- Repo-level invariant: `grep -rn 'override_logical_id(' infra/`
  shows only the legitimate `pull_through_cache.py:49` line
  (`rule.override_logical_id(construct_id)` -- portable parameter,
  not a hash).

## Rollback

If `cdk import` fails in Phase 3 or `cdk deploy` fails in Phase 4
with the orphaned physical resource still in AWS:

1. The Phase 0 backup recovery point is your safety net. Provision
   a fresh resource via the new code (no pins, no import), then
   restore from the recovery point into the new resource.
   ```
   bin/aws backup start-restore-job \
     --recovery-point-arn <from Phase 0 backup job> \
     --metadata <file-system-id=<new-fs-id>,etc> \
     --iam-role-arn <BackupPlan role>
   ```
   Restore takes ~30 minutes for a small EFS, longer for larger
   ones.

2. If Phase 2 (the disown deploy) failed before fully orphaning,
   simply revert the Phase 1 commenting and redeploy. CFN
   transactions roll back atomically; nothing should have been
   actually destroyed if the deploy reported `UPDATE_ROLLBACK_COMPLETE`.

## What this skill is *not* for

- Renames that don't involve a stateful resource. If the diff shows
  destroy/replace for stateless infrastructure (Lambdas, IAM
  policies, ECS task definitions), let CDK do its thing -- no
  migration needed.
- Adding `override_logical_id` to "fix" a diff. That's the exact
  anti-pattern this skill exists to replace.
- Fresh forks of the repo. New deploys hit the natural construct
  paths from day one with no migration needed.
