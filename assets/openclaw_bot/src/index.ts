import * as fs from "node:fs";
import * as path from "node:path";

import {
  MatrixClient,
  RustSdkCryptoStorageProvider,
  RustSdkCryptoStoreType,
  SimpleFsStorageProvider,
} from "matrix-bot-sdk";

import { forwardToGateway } from "./openclaw.js";

interface Config {
  homeserverUrl: string;
  accessToken: string;
  controlRoomId: string;
  allowedSender: string;
  dataDir: string;
  gatewayUrl: string;
  gatewayToken: string;
  rateLimitMaxPerWindow: number;
  rateLimitWindowMs: number;
}

function requireEnv(name: string): string {
  const v = process.env[name];
  if (!v) {
    throw new Error(`missing required env: ${name}`);
  }
  return v;
}

function readFileEnv(envName: string): string {
  const filePath = requireEnv(envName);
  if (!fs.existsSync(filePath)) {
    throw new Error(`${envName} points at missing file: ${filePath}`);
  }
  return fs.readFileSync(filePath, "utf8").trim();
}

function loadConfig(): Config {
  const dataDir = requireEnv("MATRIX_BOT_DATA_DIR");
  fs.mkdirSync(dataDir, { recursive: true });
  return {
    homeserverUrl: requireEnv("HOMESERVER_URL"),
    accessToken: readFileEnv("BOT_ACCESS_TOKEN_FILE"),
    controlRoomId: requireEnv("CONTROL_ROOM_ID"),
    allowedSender: requireEnv("ALLOWED_SENDER"),
    dataDir,
    gatewayUrl: requireEnv("OPENCLAW_GATEWAY_URL"),
    gatewayToken: readFileEnv("OPENCLAW_GATEWAY_TOKEN_FILE"),
    rateLimitMaxPerWindow: 6,
    rateLimitWindowMs: 60_000,
  };
}

class SlidingWindowLimiter {
  private timestamps: number[] = [];

  constructor(
    private readonly maxPerWindow: number,
    private readonly windowMs: number,
  ) {}

  tryAcquire(): boolean {
    const now = Date.now();
    this.timestamps = this.timestamps.filter((t) => now - t < this.windowMs);
    if (this.timestamps.length >= this.maxPerWindow) return false;
    this.timestamps.push(now);
    return true;
  }
}

async function main(): Promise<void> {
  const cfg = loadConfig();
  const log = (level: "info" | "warn" | "error", msg: string) => {
    console.log(JSON.stringify({ ts: new Date().toISOString(), level, msg }));
  };

  log("info", `starting bot for ${cfg.allowedSender} in ${cfg.controlRoomId}`);

  const storage = new SimpleFsStorageProvider(
    path.join(cfg.dataDir, "bot-storage.json"),
  );
  const cryptoStorage = new RustSdkCryptoStorageProvider(
    path.join(cfg.dataDir, "crypto-store"),
    RustSdkCryptoStoreType.Sqlite,
  );

  const client = new MatrixClient(
    cfg.homeserverUrl,
    cfg.accessToken,
    storage,
    cryptoStorage,
  );

  // Auto-join the control room only. Refuse all other invites.
  client.on("room.invite", async (roomId: string, invite) => {
    if (roomId !== cfg.controlRoomId) {
      log("warn", `rejecting invite to non-allowlisted room ${roomId}`);
      try {
        await client.leaveRoom(roomId);
      } catch (e) {
        log("warn", `leave failed for ${roomId}: ${(e as Error).message}`);
      }
      return;
    }
    if (invite?.sender !== cfg.allowedSender) {
      log(
        "warn",
        `rejecting invite from non-allowlisted sender ${invite?.sender}`,
      );
      try {
        await client.leaveRoom(roomId);
      } catch (e) {
        log("warn", `leave failed for ${roomId}: ${(e as Error).message}`);
      }
      return;
    }
    await client.joinRoom(roomId);
    log("info", `joined control room ${roomId}`);
  });

  const limiter = new SlidingWindowLimiter(
    cfg.rateLimitMaxPerWindow,
    cfg.rateLimitWindowMs,
  );

  client.on("room.message", async (roomId: string, event: any) => {
    // Allowlist: room
    if (roomId !== cfg.controlRoomId) return;
    // Allowlist: sender
    if (event?.sender !== cfg.allowedSender) {
      log(
        "warn",
        `ignoring message from non-allowlisted sender ${event?.sender}`,
      );
      return;
    }
    // Skip our own messages
    const me = await client.getUserId();
    if (event.sender === me) return;

    const content = event.content;
    if (!content || content.msgtype !== "m.text") return;

    // E2E enforcement: refuse plaintext. The room.message event the
    // client emits for an encrypted room is the *decrypted* event,
    // with `event.encrypted == true` (matrix-bot-sdk surfaces this
    // flag on decrypted events). A plaintext message in the control
    // room means either the room isn't E2E or someone bypassed it -
    // either way, reject.
    if (!event.encrypted) {
      log("warn", `refusing plaintext message ${event.event_id}`);
      await client.replyText(
        roomId,
        event,
        "rejected: this control room must be end-to-end encrypted.",
      );
      return;
    }

    if (!limiter.tryAcquire()) {
      log("warn", `rate limit exceeded; dropping ${event.event_id}`);
      await client.replyText(
        roomId,
        event,
        "rate limited; try again in a minute.",
      );
      return;
    }

    const prompt = (content.body as string | undefined)?.trim();
    if (!prompt) return;
    log("info", `forwarding prompt of length ${prompt.length}`);

    try {
      const response = await forwardToGateway({
        gatewayUrl: cfg.gatewayUrl,
        gatewayToken: cfg.gatewayToken,
        prompt,
      });
      await client.replyText(roomId, event, response);
    } catch (e) {
      const msg = (e as Error).message ?? String(e);
      log("error", `gateway error: ${msg}`);
      await client.replyText(roomId, event, `gateway error: ${msg}`);
    }
  });

  await client.start();
  log("info", `bot running; user_id=${await client.getUserId()}`);
}

main().catch((e) => {
  console.error("fatal:", e);
  process.exit(1);
});
