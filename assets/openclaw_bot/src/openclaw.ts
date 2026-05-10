// HTTP client for the loopback OpenClaw gateway.
//
// The gateway is bound to 127.0.0.1:18789 with token auth (set by
// `openclaw onboard ... --gateway-auth token --gateway-bind loopback`).
// Exact request/response shape is TBD - the systemd unit will pass
// the gateway URL + token, and this module abstracts the call so
// the integration can evolve without touching the bot's allowlist
// logic.

interface ForwardOpts {
  gatewayUrl: string;
  gatewayToken: string;
  prompt: string;
}

interface ChatResponse {
  // Tentative shape; revisit once the live gateway API is confirmed.
  response?: string;
  reply?: string;
  output?: string;
  error?: string;
}

export async function forwardToGateway(opts: ForwardOpts): Promise<string> {
  const url = `${opts.gatewayUrl.replace(/\/+$/, "")}/v1/chat`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${opts.gatewayToken}`,
    },
    body: JSON.stringify({ prompt: opts.prompt }),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "<no body>");
    throw new Error(`gateway HTTP ${res.status}: ${body.slice(0, 500)}`);
  }
  const data = (await res.json()) as ChatResponse;
  if (data.error) throw new Error(data.error);
  const out = data.response ?? data.reply ?? data.output;
  if (!out) {
    throw new Error(
      `gateway returned no message field: ${JSON.stringify(data).slice(0, 500)}`,
    );
  }
  return out;
}
