# Repo guidelines for agents

This repo is intended to be forkable. Anyone who clones it and
deploys to their own AWS account should get a sensible, working
infrastructure with no hand-editing required. That means CDK
source has to be portable — no values that are only meaningful
inside this specific deployment.

## Do not pin CFN logical IDs

**Never call `override_logical_id(<hardcoded-string>)` with a
deploy-state-specific value.** Examples of forbidden patterns:

```python
# BAD: a CDK-derived hash from one particular deployment
cast(efs.CfnFileSystem, fs.node.default_child).override_logical_id(
    "MatrixFs672E241B"
)

# BAD: same idea, even with a comment justifying it
sg.node.default_child.override_logical_id("EfsSecurityGroupEC5F36AC")
```

These pins are CDK's hash of a *previous* construct path,
captured from one particular deployment's CFN template. They look
like opaque hex to anyone reading the code, they break when CDK
changes its hashing rule, and they are not portable to a fresh
deploy.

The legitimate use of `override_logical_id` is when the override
value is itself portable — e.g.,
`rule.override_logical_id(construct_id)` in
`infra/constructs/pull_through_cache.py`, where the override is
the construct's own id (a parameter the caller supplies). That
pattern is fine.

## When a refactor would produce a destroy/recreate diff

Construct extractions or path renames change CDK-derived logical
IDs. Without intervention, `cdk diff` then shows the affected
resources as destroy/recreate -- fine for stateless resources,
disastrous for ones that carry data (EFS file systems, RDS
instances, S3 buckets with content).

The right answer is **not** to pin the new logical ID back to
the old one in code. The right answer is to **migrate the
deployed state** to match the new natural derivation, then leave
the source code clean.

For resource-rename migrations, follow the
`rename-cdk-resource` skill in `.claude/skills/`. It walks
through the four-phase `cdk import` flow that orphans the
existing physical resource via `RemovalPolicy.RETAIN`, then
re-adopts it under the new logical ID, with no permanent code
artifact afterward.

## Other conventions

- Commit directly to `main` by default; CI is small and the
  repo isn't gated by PRs unless explicitly asked.
- This is a personal pre-prod deployment; prefer straight-line
  changes over data-preserving migrations when the data is
  cheap to regenerate. **But** data that's expensive to
  regenerate (matrix signing keys, mail history, openclaw +
  matrix-bot crypto stores) still gets preserved through
  refactors.
- Prefer automation over manual operator steps; flag any
  required manual bootstrapping during plan mode rather than
  baking it into the deploy as a runtime prerequisite.
- Template files (`.tmpl`) live under `assets/<service>/`
  and are rendered via `AssetLoader.render_template()` (synth
  time) or `envsubst` / `python3 expandvars` (runtime in init
  containers). The format-specific validators in
  `.validator.toml` extend to `*.<ext>.tmpl` so YAML/JSON/SH
  templates get linted.
