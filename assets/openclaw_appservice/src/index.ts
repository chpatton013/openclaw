// Matrix Application Service for openclaw.
//
// Synapse pushes events here via PUT /_matrix/app/v1/transactions.
// The AS puppets `@_openclaw_<agent>` ghosts: on invite it joins
// the room as the ghost; on a message addressed to the room it
// dispatches to `openclaw agent --agent <agent>` and replies as
// the same ghost.
//
// Unencrypted rooms only -- Phase C will add pantalaimon. Until
// then, attempts to invite a ghost to an encrypted DM will reach
// the AS but messages will be undecryptable; the ghost joins, no
// reply gets through.

import * as fs from "node:fs";

import {
  Appservice,
  IAppserviceOptions,
  IAppserviceRegistration,
  LogLevel,
  LogService,
  RichConsoleLogger,
} from "matrix-bot-sdk";

import { forwardToGateway } from "./openclaw.js";

interface Config {
  homeserverUrl: string;
  homeserverName: string;
  bindAddress: string;
  port: number;
  asToken: string;
  hsToken: string;
  senderLocalpart: string;
  ghostPrefix: string;
  agentIds: string[];
  allowedSender: string;
  gatewayToken: string;
  agentTimeoutSeconds: number;
}

function requireEnv(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`missing required env: ${name}`);
  return v;
}

function readFileEnv(name: string): string {
  const p = requireEnv(name);
  if (!fs.existsSync(p)) {
    throw new Error(`${name} points at missing file: ${p}`);
  }
  return fs.readFileSync(p, "utf8").trim();
}

function loadConfig(): Config {
  return {
    homeserverUrl: requireEnv("HOMESERVER_URL"),
    homeserverName: requireEnv("HOMESERVER_NAME"),
    bindAddress: process.env.AS_BIND_ADDRESS ?? "0.0.0.0",
    port: Number(process.env.AS_PORT ?? "9000"),
    asToken: readFileEnv("APPSERVICE_AS_TOKEN_FILE"),
    hsToken: readFileEnv("APPSERVICE_HS_TOKEN_FILE"),
    senderLocalpart: process.env.AS_SENDER_LOCALPART ?? "openclaw",
    ghostPrefix: process.env.AS_GHOST_PREFIX ?? "_openclaw_",
    agentIds: requireEnv("AGENT_IDS")
      .split(",")
      .map((s) => s.trim())
      .filter((s) => s.length > 0),
    allowedSender: requireEnv("ALLOWED_SENDER"),
    gatewayToken: readFileEnv("OPENCLAW_GATEWAY_TOKEN_FILE"),
    agentTimeoutSeconds: Number(
      process.env.OPENCLAW_AGENT_TIMEOUT_SECONDS ?? "120",
    ),
  };
}

// Returns the agent id (e.g. "wadsworth") for a ghost MXID like
// "@_openclaw_wadsworth:chiiiirs.com", or null if the MXID isn't
// in our namespace.
function ghostMxidToAgentId(mxid: string, cfg: Config): string | null {
  const expectedPrefix = `@${cfg.ghostPrefix}`;
  if (!mxid.startsWith(expectedPrefix)) return null;
  const localpart = mxid.slice(1, mxid.indexOf(":"));
  if (!localpart.startsWith(cfg.ghostPrefix)) return null;
  const agentId = localpart.slice(cfg.ghostPrefix.length);
  if (!cfg.agentIds.includes(agentId)) return null;
  return agentId;
}

// Find which ghost user (and therefore which agent) is in the
// given room. The AS bot user (`@openclaw`) isn't joined to any
// agent rooms, so we can't query membership through it -- query
// each ghost's joined-rooms list instead. Ghosts that haven't
// been registered yet 401 silently; we skip those.
async function ghostInRoom(
  appservice: Appservice,
  roomId: string,
  cfg: Config,
): Promise<string | null> {
  for (const agentId of cfg.agentIds) {
    const mxid = `@${cfg.ghostPrefix}${agentId}:${cfg.homeserverName}`;
    const intent = appservice.getIntentForUserId(mxid);
    try {
      const rooms = await intent.underlyingClient.getJoinedRooms();
      if (rooms.includes(roomId)) return agentId;
    } catch (e) {
      LogService.debug(
        "openclaw-as",
        `ghostInRoom: ${agentId} probe failed (likely not registered yet): ${
          e instanceof Error ? e.message : String(e)
        }`,
      );
    }
  }
  return null;
}

async function handleRoomEvent(
  appservice: Appservice,
  cfg: Config,
  roomId: string,
  event: { type?: string; sender?: string; content?: Record<string, unknown> },
): Promise<void> {
  if (event.type !== "m.room.message") return;
  if (event.sender === undefined) return;
  if (event.sender === appservice.botUserId) return;
  if (ghostMxidToAgentId(event.sender, cfg) !== null) return; // skip our own ghosts
  if (event.sender !== cfg.allowedSender) {
    LogService.warn(
      "openclaw-as",
      `ignoring message from non-allowed sender ${event.sender} in ${roomId}`,
    );
    return;
  }
  const content = event.content;
  if (!content) return;
  const body = content.body;
  if (typeof body !== "string" || body.trim().length === 0) return;
  if (content.msgtype !== "m.text") return;

  const agentId = await ghostInRoom(appservice, roomId, cfg);
  if (agentId === null) {
    LogService.warn(
      "openclaw-as",
      `no openclaw ghost present in ${roomId}; ignoring message`,
    );
    return;
  }

  const ghostMxid = `@${cfg.ghostPrefix}${agentId}:${cfg.homeserverName}`;
  const intent = appservice.getIntentForUserId(ghostMxid);
  try {
    const reply = await forwardToGateway({
      agentId,
      prompt: body,
      timeoutSeconds: cfg.agentTimeoutSeconds,
      gatewayToken: cfg.gatewayToken,
    });
    await intent.underlyingClient.sendText(roomId, reply);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    LogService.error("openclaw-as", `agent ${agentId} dispatch failed: ${msg}`);
    // Best-effort: surface the failure inline so the operator
    // sees something in the room. If the sendText itself throws
    // (e.g. ghost can't speak), the outer catch logs it.
    try {
      await intent.underlyingClient.sendText(roomId, `error: ${msg}`);
    } catch (e2) {
      LogService.error(
        "openclaw-as",
        `failed to send error message to ${roomId}: ${
          e2 instanceof Error ? e2.message : String(e2)
        }`,
      );
    }
  }
}

async function main(): Promise<void> {
  LogService.setLogger(new RichConsoleLogger());
  LogService.setLevel(LogLevel.INFO);
  const cfg = loadConfig();

  const registration: IAppserviceRegistration = {
    as_token: cfg.asToken,
    hs_token: cfg.hsToken,
    sender_localpart: cfg.senderLocalpart,
    namespaces: {
      users: [
        {
          exclusive: true,
          regex: `@${cfg.ghostPrefix}.*:${cfg.homeserverName}`,
        },
      ],
      rooms: [],
      aliases: [
        {
          exclusive: false,
          regex: `#openclaw-.*:${cfg.homeserverName}`,
        },
      ],
    },
    id: "openclaw",
    // The url field in the registration YAML is consumed by
    // Synapse, not by us. matrix-bot-sdk's IAppserviceRegistration
    // type requires the field for completeness; set it to the
    // value Synapse already has.
    url: process.env.AS_PUBLIC_URL ?? "http://127.0.0.1:9000",
  };

  const options: IAppserviceOptions = {
    port: cfg.port,
    bindAddress: cfg.bindAddress,
    homeserverName: cfg.homeserverName,
    homeserverUrl: cfg.homeserverUrl,
    registration,
    // joinStrategy is undefined => no auto-join. We handle
    // invites explicitly so we can verify the ghost is in our
    // agent allowlist first.
  };

  const appservice = new Appservice(options);

  // Pre-register every agent's ghost user. Idempotent on Synapse's
  // side: a second register is a 200 with the same user_id.
  appservice.on("query.user", async (userId, createUser) => {
    const agentId = ghostMxidToAgentId(userId, cfg);
    if (agentId === null) {
      // Outside our namespace -- shouldn't happen if Synapse
      // respects exclusive=true. Refuse.
      return;
    }
    LogService.info("openclaw-as", `registering ghost user: ${userId}`);
    await createUser({
      displayName: agentId.charAt(0).toUpperCase() + agentId.slice(1),
    });
  });

  appservice.on("room.invite", async (roomId, event) => {
    const stateKey = event.state_key;
    if (typeof stateKey !== "string") return;
    const agentId = ghostMxidToAgentId(stateKey, cfg);
    if (agentId === null) {
      LogService.warn(
        "openclaw-as",
        `ignoring invite to non-agent ghost ${stateKey} in ${roomId}`,
      );
      return;
    }
    if (event.sender !== cfg.allowedSender) {
      LogService.warn(
        "openclaw-as",
        `refusing invite from non-allowed sender ${event.sender} in ${roomId}`,
      );
      return;
    }
    LogService.info(
      "openclaw-as",
      `joining ${roomId} as ${stateKey} (agent ${agentId})`,
    );
    await appservice.getIntentForUserId(stateKey).joinRoom(roomId);
  });

  appservice.on("room.event", (roomId, event) => {
    // matrix-bot-sdk's emitter doesn't catch promise rejections
    // from listeners; an unhandled throw here crashes the whole
    // Node process. Detach into an async closure and swallow
    // anything that escapes the inner handler.
    void handleRoomEvent(appservice, cfg, roomId, event).catch((e) => {
      LogService.error(
        "openclaw-as",
        `unhandled error in room.event handler for ${roomId}: ${
          e instanceof Error ? (e.stack ?? e.message) : String(e)
        }`,
      );
    });
  });

  await appservice.begin();
  LogService.info(
    "openclaw-as",
    `appservice listening on ${cfg.bindAddress}:${cfg.port}; agents=${cfg.agentIds.join(",")}`,
  );
}

main().catch((e) => {
  LogService.error("openclaw-as", "fatal:", e);
  process.exitCode = 1;
});
