# Matrix integration: openclaw's native channel

Matrix-side openclaw integration runs entirely inside the
openclaw daemon. No separate Node processes, no pantalaimon, no
appservice YAML, no separate matrix-bot. Each agent has its own
Matrix identity (`@openclaw-<agent>:<public_domain>`) with E2EE
on by default.

## Conventions

- **MXID naming:** `@openclaw-<agent>` (e.g. `@openclaw-wadsworth`).
  Lowercase Matrix localpart; matches the pattern previously used
  by the retired `@openclaw-bot`.
- **openclaw config key:** the openclaw-side label for each
  account is the bare agent slug (`wadsworth`, not
  `openclaw-wadsworth`); it becomes the
  `channels.matrix.accounts.<name>` key and the env-var prefix.

## Onboarding a new agent

1. Mint a Matrix user + access token:
   `bin/matrix-register-user openclaw-<agent>`
2. SSM into the openclaw EC2 instance and run `openclaw doctor`.
   In the matrix-channel section of the wizard, add an account
   under the agent slug and paste the access token.
3. Set `dm.policy: "allowlist"` with your MXID in `allowFrom`,
   and `autoJoin: dm` (or whatever value openclaw's docs name
   for "accept DM invites from allowlist") so the agent will
   join encrypted DMs you initiate from Element.

## Out of scope

- Pre-creating agent accounts via CDK. Account onboarding is
  treated like human user onboarding (e.g. Authentik users): a
  one-time manual step per agent, with the resulting tokens
  living in EFS-resident openclaw state.
- Federation behavior for agent MXIDs. Synapse's federation is
  open by default; agents become reachable from other Matrix
  servers if any peer initiates a DM. Operator-controlled
  allowlist policies (`dm.policy: "allowlist"`) prevent that
  from being a problem in practice.
