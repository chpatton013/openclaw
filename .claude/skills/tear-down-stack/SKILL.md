---
name: tear-down-stack
description: Tear down a CDK stack safely in this repo. Walks through the destructive-but-correct sequence (disable RDS deletion protection, delete the stack, wait, then clean up RETAIN'd resources by hand) and the recovery path when CloudFormation lands in DELETE_FAILED. Knows which stacks have retained data (RDS, EFS, S3 buckets) and refuses to tear down producer stacks (Foundation, Data) without confirming the operator wants to nuke the whole deployment.
allowed-tools: Bash
---

# Tear Down a CDK Stack

Stack to tear down: **$ARGUMENTS**

If `$ARGUMENTS` is empty, ask the user which stack name to tear down before
proceeding. Valid stack names in this repo: `FoundationStack`, `DataStack`,
`AuthentikStack`, `HeadscaleStack`, `VaultwardenStack`, `OpenClawStack`,
`WebfingerStack`.

---

## WARNING — read before doing anything

This skill is **destructive**. CloudFormation will delete every non-RETAIN'd
resource in the stack the moment `delete-stack` is issued. After confirming
each step with the operator:

- An RDS instance with `deletion_protection` flipped off WILL be deleted (a
  final snapshot will be taken because the underlying CDK construct's default
  removal policy for a `rds.DatabaseInstance` is `SNAPSHOT`, not `DESTROY` —
  but that snapshot still costs storage and is the only path back to the data).
- S3 buckets and EFS volumes marked `RemovalPolicy.RETAIN` will survive the
  stack delete and continue billing until you delete them by hand.
- Cross-stack exports break the moment a producer stack starts deleting. If
  any consumer stack still imports from this stack, CloudFormation will refuse
  the delete with `Export ... cannot be deleted as it is in use`.

**At every step, confirm with the operator before issuing the destructive
command.** If you're unsure whether the operator wants the data gone, abort
and ask. This is pre-prod personal infra (per the user's memory) — straight-
line tear-down is fine — but data the operator didn't expect to lose (e.g.
the Authentik users in the shared RDS) is still a real loss.

---

## Step 0 — Refuse to tear down producer stacks casually

If `$ARGUMENTS` is `FoundationStack` or `DataStack`, **stop and warn**:

- **FoundationStack** owns the VPC, ECS cluster, and ECR pull-through cache
  rules used by every other stack. Deleting it tears down the entire AWS
  deployment.
- **DataStack** owns the shared Postgres RDS instance used by AuthentikStack,
  HeadscaleStack, and VaultwardenStack. Deleting it deletes (or snapshots)
  the database that holds users, sessions, machines, vault items.

The right way to retire either of these is to tear down every consumer stack
first, then tear down the producer. Confirm with the operator that this is
their intent before continuing.

The hosted zones in FoundationStack are **imported via `route53.HostedZone.
from_lookup`** — they are not managed by CloudFormation, so deleting
FoundationStack will NOT delete the hosted zones (good — public DNS keeps
working, the operator's registrar NS records stay valid). The hosted zones
only go away if the operator deletes them manually in Route53, which would
break public DNS for the entire deployment.

---

## Step 1 — Pre-flight checklist

Confirm each item with the operator before continuing.

### 1a. RETAIN'd resources in the target stack

The skill code-walked the stacks. Here's what survives a stack delete and
needs manual cleanup if the operator truly wants it gone:

| Stack            | RETAIN'd resources                                                                  |
| ---------------- | ----------------------------------------------------------------------------------- |
| FoundationStack  | (Hosted zones are imported — not managed by CFN, survive automatically.)            |
| DataStack        | RDS instance: `deletion_protection=True` by CDK default; `removal_policy=SNAPSHOT`. |
| AuthentikStack   | S3 `Bucket` (media), S3 `BlueprintsBucket` — both `RemovalPolicy.RETAIN`.           |
| HeadscaleStack   | None in-stack. (Consumes shared RDS owned by DataStack.)                            |
| VaultwardenStack | EFS `DataFs` — `RemovalPolicy.RETAIN`. (Consumes shared RDS owned by DataStack.)    |
| OpenClawStack    | None retained (EFS is `RemovalPolicy.DESTROY`); but AWS Backup vault recovery       |
|                  | points from the `openclaw-efs-backups` plan persist in the backup vault.            |
| WebfingerStack   | None.                                                                               |

For the target stack, list the matching rows and ask the operator: *"After
the stack delete completes, do you want me to clean these up too, or leave
them in place?"*

### 1b. Stack state must be clean

```bash
bin/aws cloudformation describe-stacks --stack-name "$ARGUMENTS" \
  --query 'Stacks[0].{Status:StackStatus,Reason:StackStatusReason}' \
  --output json
```

Acceptable starting states: `CREATE_COMPLETE`, `UPDATE_COMPLETE`,
`UPDATE_ROLLBACK_COMPLETE`, `ROLLBACK_COMPLETE`, `DELETE_FAILED`.

If the stack is wedged (`*_IN_PROGRESS`, `UPDATE_ROLLBACK_FAILED`), recover it
to a terminal state first:
- `*_IN_PROGRESS`: wait for it to settle, or cancel via
  `cancel-update-stack` if it's an update.
- `UPDATE_ROLLBACK_FAILED`: `bin/aws cloudformation continue-update-rollback
  --stack-name <name> [--resources-to-skip <logical-id>]` (see the
  `debug-exit-node` skill for the canonical example with `ExitNodeRoutes`).
- **Important**: any resource that was previously skipped via
  `--resources-to-skip` is left in a "stale" state and will block subsequent
  *delete* attempts the same way it blocked the rollback. If the operator
  used `--resources-to-skip` recently, expect to see those same resources in
  Step 5's `DELETE_FAILED` list.

### 1c. Downstream consumers

```bash
# Look for stacks that import from this one.
bin/aws cloudformation list-exports \
  --query "Exports[?starts_with(ExportingStackId, \`arn:\`) && contains(ExportingStackId, \`/${ARGUMENTS}/\`)]" \
  --output json
```

For each export, the `ImportingStacks` list (from
`list-imports --export-name <name>`) tells you which stacks still consume it.
**If the list is non-empty, the delete will fail.** Tear those consumer
stacks down first, in dependency order:

- AuthentikStack, HeadscaleStack, VaultwardenStack, WebfingerStack all
  consume FoundationStack and (where applicable) DataStack.
- OpenClawStack is mostly self-contained but read its `imports` to confirm.

---

## Step 2 — Tear-down sequence

Confirm with the operator before each command.

### 2a. Disable RDS deletion protection (DataStack only)

Skip this for any stack other than DataStack. The shared RDS is the only
instance in this repo.

```bash
# Resolve the DB instance identifier from the stack.
DB_ID=$(bin/aws cloudformation describe-stack-resources \
  --stack-name DataStack \
  --logical-resource-id DatabaseInstanceXXXX \
  --query 'StackResources[0].PhysicalResourceId' --output text)
# (Or list AWS::RDS::DBInstance resources in the stack if you don't know the
# logical ID.)

bin/aws rds modify-db-instance \
  --db-instance-identifier "$DB_ID" \
  --no-deletion-protection \
  --apply-immediately

# Wait for the modify to land before deleting the stack.
bin/aws rds wait db-instance-available --db-instance-identifier "$DB_ID"
```

**Confirm with the operator one more time** before proceeding — once
deletion protection is off, the next step deletes the database.

### 2b. Delete the stack

```bash
bin/aws cloudformation delete-stack --stack-name "$ARGUMENTS"
```

### 2c. Wait for completion

```bash
bin/aws cloudformation wait stack-delete-complete --stack-name "$ARGUMENTS"
echo "exit=$?"   # 0 = stack deleted; non-zero = DELETE_FAILED, see Step 5
```

If the wait fails, jump to **Step 5 — Recovery from DELETE_FAILED**.

### 2d. Clean up RETAIN'd resources (only if the operator confirmed)

Get explicit confirmation per resource. The operator may want to keep some.

**S3 buckets (AuthentikStack):**
```bash
# Empty first (versioned buckets need delete-markers cleared too).
bin/aws s3 rm "s3://${BUCKET_NAME}" --recursive
bin/aws s3api delete-bucket --bucket "$BUCKET_NAME"
```
For versioned buckets, you may need to delete versions and delete-markers
explicitly via `list-object-versions` + `delete-objects`.

**EFS volume (VaultwardenStack):**
```bash
# Delete mount targets first.
for MT in $(bin/aws efs describe-mount-targets --file-system-id "$FS_ID" \
              --query 'MountTargets[].MountTargetId' --output text); do
  bin/aws efs delete-mount-target --mount-target-id "$MT"
done
bin/aws efs delete-file-system --file-system-id "$FS_ID"
```

**RDS final snapshot (DataStack, if `removal_policy=SNAPSHOT` was honored):**
```bash
# List snapshots created by the stack delete.
bin/aws rds describe-db-snapshots \
  --snapshot-type manual \
  --query "DBSnapshots[?starts_with(DBSnapshotIdentifier, \`datastack\`)]"

# Delete only after the operator confirms they don't want a restore path.
bin/aws rds delete-db-snapshot --db-snapshot-identifier "$SNAPSHOT_ID"
```

**AWS Backup recovery points (OpenClawStack):**
```bash
# List recovery points in the openclaw vault.
VAULT=$(bin/aws backup list-backup-vaults \
  --query 'BackupVaultList[?contains(BackupVaultName, `OpenClaw`)].BackupVaultName | [0]' \
  --output text)
bin/aws backup list-recovery-points-by-backup-vault --backup-vault-name "$VAULT"

# Delete each recovery point, then the vault.
bin/aws backup delete-recovery-point --backup-vault-name "$VAULT" \
  --recovery-point-arn "$RP_ARN"
bin/aws backup delete-backup-vault --backup-vault-name "$VAULT"
```

**Hosted zones (NEVER delete unless retiring the whole deployment):**
The hosted zones are imported into FoundationStack, not managed by it. They
hold the NS records the operator's domain registrar points to. Deleting a
hosted zone breaks public DNS for every service in the deployment until the
operator re-creates it AND updates the registrar's NS records to the new
delegation set. Only do this if the operator is retiring the entire AWS
account.

---

## Step 3 — Verify the stack is gone

```bash
bin/aws cloudformation describe-stacks --stack-name "$ARGUMENTS" 2>&1 \
  | grep -E 'does not exist|ValidationError'
```

Expected output: `Stack with id <name> does not exist`. If the stack is
still listed, re-check its status — likely `DELETE_FAILED`.

---

## Step 4 — Recovery from `DELETE_FAILED`

```bash
bin/aws cloudformation describe-stack-events --stack-name "$ARGUMENTS" \
  --query 'StackEvents[?ResourceStatus==`DELETE_FAILED`].{
      Logical:LogicalResourceId,
      Type:ResourceType,
      Reason:ResourceStatusReason
    }' \
  --output json
```

Match the failure reason to the canonical cause:

| Symptom in `Reason`                                        | Cause                                                         | Fix                                                                                            |
| ---------------------------------------------------------- | ------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `Cannot delete protected ...`                              | RDS deletion protection still on                              | Step 2a, then retry delete.                                                                    |
| `network interface ... is currently in use`                | ENI still attached (Lambda or Fargate task); SG can't drop    | Wait 10–15 min for ENI cleanup, then retry. If stuck, find the ENI and force-detach.           |
| `The bucket you tried to delete is not empty`              | S3 bucket has objects (or versions / delete-markers)          | Empty the bucket including all versions, then retry — or `--retain-resources` on the bucket.   |
| `Resource handler returned message: ... DBSnapshot ...`    | Final snapshot creation conflicting with an existing snapshot | Delete the conflicting snapshot, then retry.                                                   |
| `Custom Resource ... did not respond`                      | Skipped Custom Resource from a prior `--resources-to-skip`    | Use `--retain-resources` on that logical ID; clean up the underlying resource by hand.         |
| `Export ... cannot be deleted as it is in use`             | A consumer stack still imports an output of this stack        | Tear down the consumer first; do NOT use `--retain-resources` for the export-producing resource. |

To retry while skipping stuck resources:

```bash
bin/aws cloudformation delete-stack \
  --stack-name "$ARGUMENTS" \
  --retain-resources LogicalId1 LogicalId2 ...

bin/aws cloudformation wait stack-delete-complete --stack-name "$ARGUMENTS"
```

Then clean up the retained resources by hand using the same commands as
Step 2d. Confirm with the operator before each manual delete.

---

## Step 5 — Final report

After the stack is deleted, report to the operator:
- Stack: deleted (final stack status).
- RETAIN'd resources: which were cleaned up, which are still in the account
  (with names so the operator can find them later).
- Anything left for the operator to do (e.g. delete a hosted zone, point a
  registrar elsewhere, remove a CDK context entry).
