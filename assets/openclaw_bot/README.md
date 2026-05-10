# OpenClaw Matrix bot

Bridges a single Matrix control room to the loopback OpenClaw
gateway on the same EC2 host. Defense-in-depth lives entirely in
this process: an attacker who breaches Synapse still has to get
past the allowlist before a message reaches OpenClaw.

## Required env (set by the systemd unit)

| Var | Source | Notes |
|---|---|---|
| `HOMESERVER_URL` | hardcoded | `https://matrix.<public_domain>` |
| `BOT_ACCESS_TOKEN_FILE` | tmpfs | the unit's `ExecStartPre` writes Secrets Manager `matrix/openclaw-bot-token`'s `token` field here |
| `CONTROL_ROOM_ID` | SSM Parameter `/openclaw-matrix-bot/control-room-id` | one room ID, set manually after first inviting the bot |
| `ALLOWED_SENDER` | hardcoded in unit | exactly `@chris:<public_domain>`; federated impersonation of a local MXID is impossible by Matrix spec |
| `MATRIX_BOT_DATA_DIR` | EFS mount | `/data/matrix-bot/`; persists Olm device keys + sync token across instance replacements |
| `OPENCLAW_GATEWAY_URL` | hardcoded | `http://127.0.0.1:18789` |
| `OPENCLAW_GATEWAY_TOKEN_FILE` | local fs | discovered post-deploy via SSM session into the OpenClaw EC2 host (path varies by openclaw version) |

## Allowlist

- Reject any room other than `CONTROL_ROOM_ID`.
- Reject any sender other than `ALLOWED_SENDER`.
- Reject plaintext events (E2E required).
- Sliding window rate limit: 6 messages / 60 seconds.

## E2E setup (one-time, manual)

After the bot first connects with its access token:

1. From your Element client, open the control room. The bot's device
   shows as unverified.
2. Right-click the bot's avatar → "Verify" → "Verify with emoji".
3. Compare emoji; confirm.
4. The cross-signing key is stored in
   `${MATRIX_BOT_DATA_DIR}/crypto-store/`. Subsequent restarts trust
   the existing identity and don't need re-verification.

If the bot crypto-store is wiped (e.g. EFS reset), re-verify once.

## Local development

```sh
pnpm install
pnpm run build
HOMESERVER_URL=https://matrix.example.com \
BOT_ACCESS_TOKEN_FILE=./.token \
CONTROL_ROOM_ID='!xyz:example.com' \
ALLOWED_SENDER='@you:example.com' \
MATRIX_BOT_DATA_DIR=./.bot-data \
OPENCLAW_GATEWAY_URL=http://127.0.0.1:18789 \
OPENCLAW_GATEWAY_TOKEN_FILE=./.gateway-token \
pnpm run start
```

## Gateway protocol

The HTTP shape `forwardToGateway()` expects (in `src/openclaw.ts`)
is tentative pending confirmation against the running daemon's
actual API. Update the URL path / request schema there once the
gateway version is known; the rest of the bot is gateway-agnostic.
