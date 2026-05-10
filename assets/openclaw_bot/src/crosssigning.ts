import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";

import { MatrixClient } from "matrix-bot-sdk";

// On first start, hand-roll a Matrix cross-signing identity for
// the bot user and upload it to Synapse. matrix-bot-sdk's bundled
// rust-crypto adapter (v0.4.0) silently drops the upload requests
// from `bootstrapCrossSigning`, so Element shows "User
// verification unavailable" against the bot. Modern Synapse
// (>=1.118, MSC3967) accepts the first cross-signing upload
// without UIA, so a fresh bot account can publish without
// password / SSO interactivity.
//
// State persisted under <dataDir>/cross-signing.json so a restart
// can re-sign new devices if they ever rotate. Single-device bot
// in practice never rotates, but cheap insurance.

interface KeyPair {
  pub: Uint8Array;
  priv: Uint8Array;
}

interface CrossSigningKeys {
  master: KeyPair;
  selfSigning: KeyPair;
  userSigning: KeyPair;
}

interface DeviceKeys {
  user_id: string;
  device_id: string;
  algorithms: string[];
  keys: Record<string, string>;
  signatures?: Record<string, Record<string, string>>;
  unsigned?: unknown;
}

// PKCS#8 ASN.1 prefix for an Ed25519 private key (RFC 8410), with
// the 32-byte seed appended verbatim it forms a complete v1 PKCS#8
// document Node's `crypto.createPrivateKey` accepts.
const ED25519_PKCS8_PREFIX = Buffer.from(
  "302e020100300506032b657004220420",
  "hex",
);

function genKeyPair(): KeyPair {
  const kp = crypto.generateKeyPairSync("ed25519");
  const pubJwk = kp.publicKey.export({ format: "jwk" });
  const privJwk = kp.privateKey.export({ format: "jwk" });
  return {
    pub: Buffer.from(pubJwk.x as string, "base64url"),
    priv: Buffer.from(privJwk.d as string, "base64url"),
  };
}

function privKeyObject(seed: Uint8Array): crypto.KeyObject {
  return crypto.createPrivateKey({
    key: Buffer.concat([ED25519_PKCS8_PREFIX, Buffer.from(seed)]),
    format: "der",
    type: "pkcs8",
  });
}

function signEd25519(privSeed: Uint8Array, message: Buffer): Buffer {
  return crypto.sign(null, message, privKeyObject(privSeed));
}

// Matrix "unpadded base64": standard base64 with `=` padding
// stripped. NOT base64url (no `-` / `_` substitution).
function b64u(bytes: Uint8Array): string {
  return Buffer.from(bytes).toString("base64").replace(/=+$/, "");
}

// Canonical JSON per https://spec.matrix.org/v1.11/appendices/#canonical-json:
// keys sorted lexically, no whitespace, UTF-8.
function canonicalJson(obj: unknown): string {
  if (obj === null || typeof obj !== "object") return JSON.stringify(obj);
  if (Array.isArray(obj)) {
    return "[" + obj.map(canonicalJson).join(",") + "]";
  }
  const o = obj as Record<string, unknown>;
  const keys = Object.keys(o).sort();
  return (
    "{" +
    keys.map((k) => JSON.stringify(k) + ":" + canonicalJson(o[k])).join(",") +
    "}"
  );
}

// Sign an object per the Matrix signing rules: strip `signatures`
// and `unsigned`, canonical JSON, ed25519 sign, base64 the
// 64-byte signature.
function signObject(obj: object, privSeed: Uint8Array): string {
  const o = obj as Record<string, unknown>;
  const { signatures: _s, unsigned: _u, ...rest } = o;
  const sig = signEd25519(privSeed, Buffer.from(canonicalJson(rest), "utf8"));
  return b64u(sig);
}

function persistKeys(dataDir: string, keys: CrossSigningKeys): void {
  const file = path.join(dataDir, "cross-signing.json");
  const json = {
    version: 1,
    master: { pub: b64u(keys.master.pub), priv: b64u(keys.master.priv) },
    self_signing: {
      pub: b64u(keys.selfSigning.pub),
      priv: b64u(keys.selfSigning.priv),
    },
    user_signing: {
      pub: b64u(keys.userSigning.pub),
      priv: b64u(keys.userSigning.priv),
    },
  };
  fs.writeFileSync(file, JSON.stringify(json), { mode: 0o600 });
}

function loadKeys(dataDir: string): CrossSigningKeys | null {
  const file = path.join(dataDir, "cross-signing.json");
  if (!fs.existsSync(file)) return null;
  const json = JSON.parse(fs.readFileSync(file, "utf8"));
  const decode = (s: string) => Buffer.from(s, "base64");
  return {
    master: { pub: decode(json.master.pub), priv: decode(json.master.priv) },
    selfSigning: {
      pub: decode(json.self_signing.pub),
      priv: decode(json.self_signing.priv),
    },
    userSigning: {
      pub: decode(json.user_signing.pub),
      priv: decode(json.user_signing.priv),
    },
  };
}

async function fetchOwnKeys(
  client: MatrixClient,
  userId: string,
): Promise<{ masterPub: string | null; device: DeviceKeys | null }> {
  const resp = (await client.doRequest(
    "POST",
    "/_matrix/client/v3/keys/query",
    null,
    { device_keys: { [userId]: [] } },
  )) as {
    master_keys?: Record<string, { keys: Record<string, string> }>;
    device_keys?: Record<string, Record<string, DeviceKeys>>;
  };
  const masterEntry = resp.master_keys?.[userId];
  // Master key id is `ed25519:<pubkey>`; the value is the pubkey.
  const masterPub = masterEntry
    ? (Object.values(masterEntry.keys)[0] ?? null)
    : null;
  const deviceId = (client.crypto as unknown as { clientDeviceId: string })
    .clientDeviceId;
  const device = resp.device_keys?.[userId]?.[deviceId] ?? null;
  return { masterPub, device };
}

function deviceIsCrossSigned(
  device: DeviceKeys,
  userId: string,
  sskPubB64: string,
): boolean {
  const userSigs = device.signatures?.[userId];
  if (!userSigs) return false;
  return Object.prototype.hasOwnProperty.call(userSigs, `ed25519:${sskPubB64}`);
}

export async function ensureCrossSigning(
  client: MatrixClient,
  dataDir: string,
  log: (level: "info" | "warn" | "error", msg: string) => void,
): Promise<void> {
  const userId = await client.getUserId();
  const { masterPub, device } = await fetchOwnKeys(client, userId);

  // Load or generate the keys, deciding based on Synapse's view +
  // local persistence.
  let keys = loadKeys(dataDir);
  if (masterPub) {
    if (!keys || b64u(keys.master.pub) !== masterPub) {
      log(
        "warn",
        "Synapse has a master key but our local cross-signing.json doesn't match; not re-uploading",
      );
      return;
    }
  } else {
    if (keys) {
      log(
        "warn",
        "local cross-signing.json present but no master key on Synapse; re-uploading from local",
      );
    } else {
      log("info", "generating new cross-signing keys");
      keys = {
        master: genKeyPair(),
        selfSigning: genKeyPair(),
        userSigning: genKeyPair(),
      };
      persistKeys(dataDir, keys);
    }
  }

  const masterPubB64 = b64u(keys.master.pub);
  const sskPubB64 = b64u(keys.selfSigning.pub);
  const uskPubB64 = b64u(keys.userSigning.pub);

  if (!masterPub) {
    // First upload: master + self-signing + user-signing all at
    // once. Self/user signing keys carry a master signature so
    // the server's chain check passes.
    const masterKey = {
      user_id: userId,
      usage: ["master"],
      keys: { [`ed25519:${masterPubB64}`]: masterPubB64 },
    };
    const sskBase = {
      user_id: userId,
      usage: ["self_signing"],
      keys: { [`ed25519:${sskPubB64}`]: sskPubB64 },
    };
    const uskBase = {
      user_id: userId,
      usage: ["user_signing"],
      keys: { [`ed25519:${uskPubB64}`]: uskPubB64 },
    };
    const selfSigningKey = {
      ...sskBase,
      signatures: {
        [userId]: {
          [`ed25519:${masterPubB64}`]: signObject(sskBase, keys.master.priv),
        },
      },
    };
    const userSigningKey = {
      ...uskBase,
      signatures: {
        [userId]: {
          [`ed25519:${masterPubB64}`]: signObject(uskBase, keys.master.priv),
        },
      },
    };
    log("info", "uploading cross-signing public keys");
    await client.doRequest(
      "POST",
      "/_matrix/client/v3/keys/device_signing/upload",
      null,
      {
        master_key: masterKey,
        self_signing_key: selfSigningKey,
        user_signing_key: userSigningKey,
      },
    );
  }

  // Sign the bot's running device with the self-signing key, if
  // not already done. Lets Element show the bot's session as
  // verified once the user has cross-signed `@openclaw-bot`'s
  // master key (which happens via "Verify user" in Element).
  if (device && !deviceIsCrossSigned(device, userId, sskPubB64)) {
    log("info", `signing device ${device.device_id} with self-signing key`);
    const deviceSig = signObject(device, keys.selfSigning.priv);
    const signedDevice = {
      ...device,
      signatures: {
        ...(device.signatures ?? {}),
        [userId]: {
          ...(device.signatures?.[userId] ?? {}),
          [`ed25519:${sskPubB64}`]: deviceSig,
        },
      },
    };
    await client.doRequest(
      "POST",
      "/_matrix/client/v3/keys/signatures/upload",
      null,
      { [userId]: { [device.device_id]: signedDevice } },
    );
  }

  log("info", "cross-signing setup complete");
}
