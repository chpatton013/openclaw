---
name: debug-stack
description: Debug a CloudFormation stack deployment. Use when a stack is stuck, failing, or in an unexpected state. Gathers CF events, ECS task health and logs, RDS status, and ALB target health, then produces a diagnosis and recommended action.
allowed-tools: Bash
---

# Debug a CloudFormation Stack

Stack to debug: **$ARGUMENTS**

If `$ARGUMENTS` is empty, ask the user which stack name to debug before proceeding.

Work through the steps below in order. Each step uses IDs discovered in the previous one.
Prefer running independent commands in parallel. Summarize findings as you go so the user
can follow along, then end with a diagnosis (Step 6).

---

## Step 1 — CloudFormation: overall state

Run in parallel:

```bash
# Stack status + status reason
aws cloudformation describe-stacks \
  --stack-name "$ARGUMENTS" \
  --query 'Stacks[0].{Status:StackStatus,Reason:StackStatusReason,Updated:LastUpdatedTime}' \
  --output json

# All resources: use physical IDs in subsequent steps
aws cloudformation describe-stack-resources \
  --stack-name "$ARGUMENTS" \
  --output json

# Recent events — focus on FAILED and IN_PROGRESS resources
aws cloudformation describe-stack-events \
  --stack-name "$ARGUMENTS" \
  --query 'StackEvents[0:40]' \
  --output json
```

From the resources output, note the `PhysicalResourceId` for each:
- `AWS::ECS::Service` entries → use as service ARNs in Step 2
- `AWS::RDS::DBInstance` entries → use as DB instance identifiers in Step 3
- `AWS::ElasticLoadBalancingV2::TargetGroup` entries → use as target group ARNs in Step 4
- `AWS::ECS::Cluster` or cross-stack cluster reference → use as cluster ARN in Step 2

If the stack doesn't exist (e.g. was deleted or never created), report that immediately and stop.

---

## Step 2 — ECS: service health and failed tasks

For each ECS service identified in Step 1, run:

```bash
aws ecs describe-services \
  --cluster CLUSTER_ARN \
  --services SERVICE_ARN \
  --query 'services[0].{
      Status:status,
      Running:runningCount,
      Desired:desiredCount,
      Pending:pendingCount,
      Events:events[0:5],
      Deployments:deployments,
      TaskDefinition:taskDefinition
    }' \
  --output json
```

For any service where `runningCount < desiredCount` or a deployment is failing/stuck, fetch stopped tasks:

```bash
# List recent stopped tasks for this service
aws ecs list-tasks \
  --cluster CLUSTER_ARN \
  --service-name SERVICE_ARN \
  --desired-status STOPPED \
  --output json
```

Then describe those tasks (up to 5 most recent):

```bash
aws ecs describe-tasks \
  --cluster CLUSTER_ARN \
  --tasks TASK_ARN_1 TASK_ARN_2 ... \
  --query 'tasks[*].{
      TaskArn:taskArn,
      StopCode:stopCode,
      StopReason:stoppedReason,
      Containers:containers[*].{
        Name:name,
        ExitCode:exitCode,
        Reason:reason,
        HealthStatus:healthStatus,
        LastStatus:lastStatus
      }
    }' \
  --output json
```

Key stop codes and what they mean:
- `TaskFailedToStart` — container never became healthy (health check failure or crash at startup)
- `EssentialContainerExited` — container ran then crashed
- `ServiceSchedulerInitiated` — ECS replaced a task proactively

For any task with `TaskFailedToStart`, fetch its logs in Step 5 — this is almost always the root cause.

---

## Step 3 — RDS: instance health

For each RDS DBInstance from Step 1:

```bash
aws rds describe-db-instances \
  --db-instance-identifier DB_INSTANCE_ID \
  --query 'DBInstances[0].{
      Status:DBInstanceStatus,
      DeletionProtection:DeletionProtection,
      Engine:Engine,
      EngineVersion:EngineVersion,
      Endpoint:Endpoint,
      MultiAZ:MultiAZ
    }' \
  --output json
```

Note: RDS deletion protection being `true` will block stack deletion. Report this explicitly if the stack is being torn down.

---

## Step 4 — ALB: target health

For each target group from Step 1:

```bash
aws elbv2 describe-target-health \
  --target-group-arn TARGET_GROUP_ARN \
  --output json
```

Target states to watch for:
- `unhealthy` — the container is reachable but failing the HTTP health check path
- `unused` — no registered targets yet (ECS hasn't registered the task)
- `draining` — deregistering; could indicate a recent task replacement

---

## Step 5 — CloudWatch Logs: container output for failed tasks

For each stopped task from Step 2 with a failure stop code, get the log stream. The log group
and stream prefix come from the task definition's `logConfiguration`:

```bash
# Inspect task definition log config (run once per task definition)
aws ecs describe-task-definition \
  --task-definition TASK_DEF_ARN \
  --query 'taskDefinition.containerDefinitions[*].{
      Name:name,
      LogGroup:logConfiguration.options."awslogs-group",
      StreamPrefix:logConfiguration.options."awslogs-stream-prefix"
    }' \
  --output json
```

The log stream name is: `STREAM_PREFIX/CONTAINER_NAME/TASK_ID`
where `TASK_ID` is the last segment of the task ARN (after the final `/`).

```bash
aws logs get-log-events \
  --log-group-name LOG_GROUP_NAME \
  --log-stream-name "STREAM_PREFIX/CONTAINER_NAME/TASK_ID" \
  --limit 100 \
  --query 'events[*].message' \
  --output json
```

If the log stream doesn't exist yet, the task likely crashed before writing any logs — report that.

For recently stopped tasks, also try listing streams to confirm the exact name:

```bash
aws logs describe-log-streams \
  --log-group-name LOG_GROUP_NAME \
  --log-stream-name-prefix "STREAM_PREFIX/CONTAINER_NAME/" \
  --order-by LastEventTime \
  --descending \
  --max-items 5 \
  --output json
```

---

## Step 6 — Diagnosis

After gathering everything, produce a structured report:

### Current state
One sentence per resource: stack status, each ECS service (running/desired), RDS status, ALB target health.

### Root cause
Identify the specific failure:
- Cite the exact log line, exit code, stop reason, or health check response that points to the root cause
- Distinguish between: connectivity failures (DB unreachable), auth failures (wrong credentials / SSL mode),
  health check misconfiguration (wrong command or endpoint), resource limits (OOM, CPU throttle),
  and infrastructure not ready (DB still initializing)

### Recommended action
The exact next step — either a command to run or a code change to make. If destructive, say so.

Common patterns seen in this project:
- **Stack stuck in `CREATE_IN_PROGRESS`**: Cannot update — must delete and redeploy.
  If RDS has deletion protection, disable it first:
  `aws rds modify-db-instance --db-instance-identifier ID --no-deletion-protection --apply-immediately`
  Then: `aws cloudformation delete-stack --stack-name NAME && aws cloudformation wait stack-delete-complete --stack-name NAME`
- **ECS tasks failing health check**: Check the health check command, start_period, and whether the
  container process is actually reachable on the expected port.
- **DB connection errors**: Verify `SSLMODE`, credentials in Secrets Manager, and that the ECS
  security group can reach the RDS security group on port 5432.
- **Deployment stuck (no circuit breaker)**: Tasks keep cycling but CloudFormation never declares failure.
  Enable `DeploymentCircuitBreaker` in the CDK construct and redeploy.
