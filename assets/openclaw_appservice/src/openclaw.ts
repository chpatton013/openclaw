// Bridge to the OpenClaw agent via the `openclaw` CLI subprocess.
// Same shape as openclaw_bot/src/openclaw.ts -- the agent CLI
// hasn't changed; the AS just calls it once per agent dispatch.

import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

interface ForwardOpts {
  agentId: string;
  prompt: string;
  timeoutSeconds: number;
  gatewayToken: string;
}

const REPLY_FIELDS = [
  "reply",
  "message",
  "response",
  "text",
  "output",
  "content",
];

function extractReply(parsed: unknown): string | null {
  if (typeof parsed === "string") return parsed;
  if (!parsed || typeof parsed !== "object") return null;
  const obj = parsed as Record<string, unknown>;
  const result = obj.result;
  if (result && typeof result === "object") {
    const payloads = (result as Record<string, unknown>).payloads;
    if (Array.isArray(payloads)) {
      const texts: string[] = [];
      for (const p of payloads) {
        if (p && typeof p === "object") {
          const t = (p as Record<string, unknown>).text;
          if (typeof t === "string" && t.trim().length > 0) {
            texts.push(t);
          }
        }
      }
      if (texts.length > 0) return texts.join("\n\n");
    }
  }
  for (const f of REPLY_FIELDS) {
    const v = obj[f];
    if (typeof v === "string" && v.trim().length > 0) return v;
  }
  return null;
}

export async function forwardToGateway(opts: ForwardOpts): Promise<string> {
  const args = [
    "agent",
    "--agent",
    opts.agentId,
    "--message",
    opts.prompt,
    "--json",
    "--timeout",
    String(opts.timeoutSeconds),
  ];
  let stdout = "";
  let stderr = "";
  try {
    const env: NodeJS.ProcessEnv = { ...process.env };
    delete env.OPENCLAW_GATEWAY_URL;
    delete env.OPENCLAW_GATEWAY_TOKEN_FILE;
    delete env.OPENCLAW_GATEWAY_PASSWORD;
    delete env.OPENCLAW_GATEWAY_PASSWORD_FILE;
    env.OPENCLAW_GATEWAY_TOKEN = opts.gatewayToken;
    const res = await execFileAsync("openclaw", args, {
      timeout: (opts.timeoutSeconds + 15) * 1000,
      maxBuffer: 4 * 1024 * 1024,
      encoding: "utf8",
      env,
    });
    stdout = res.stdout ?? "";
    stderr = res.stderr ?? "";
  } catch (e) {
    const err = e as { stdout?: string; stderr?: string; message: string };
    stdout = err.stdout ?? "";
    stderr = err.stderr ?? "";
    const detail = stderr.trim() || stdout.trim() || err.message;
    throw new Error(`openclaw agent failed: ${detail.slice(0, 800)}`);
  }
  const trimmed = stdout.trim();
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      const reply = extractReply(JSON.parse(trimmed));
      if (reply) return reply;
    } catch {
      // fall through to plain-text handling
    }
  }
  if (trimmed) return trimmed;
  if (stderr.trim()) {
    throw new Error(`openclaw agent: ${stderr.trim().slice(0, 800)}`);
  }
  throw new Error("openclaw agent returned empty output");
}
