import { randomBytes, randomUUID } from "node:crypto";
import readline from "node:readline";
import { Impit } from "impit";

const BASE = "https://blinkit.com";
const REQ_KEY = process.env.BLINKIT_REQ_KEY ?? "c0e6868e-1180-400c-be51-f473479f1f0a";
const deviceId = randomBytes(8).toString("hex");
const sessionUuid = randomUUID();
const impit = new Impit({ browser: "chrome" });
let authKey;

const STATIC_HEADERS = {
  app_client: "consumer_web",
  platform: "desktop_web",
  web_app_version: "1008010016",
  rn_bundle_version: "1009003012",
  app_version: "52434332",
  "x-age-consent-granted": "false",
  "user-agent":
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
  accept: "*/*",
  "accept-language": "en-US,en;q=0.9",
  origin: BASE,
  referer: `${BASE}/`,
};

function makeUrl(path, params = {}) {
  const url = new URL(path.startsWith("http") ? path : BASE + path);
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
  }
  return url.toString();
}

async function rawRequest({ method = "GET", path, params, jsonBody, lat, lon, headers = {} }) {
  const requestHeaders = {
    ...STATIC_HEADERS,
    device_id: deviceId,
    session_uuid: sessionUuid,
    lat: String(lat),
    lon: String(lon),
    cookie: `gr_1_deviceId=${deviceId}; gr_1_lat=${lat}; gr_1_lon=${lon}`,
    ...headers,
  };
  const body = jsonBody === undefined || jsonBody === null ? undefined : JSON.stringify(jsonBody);
  if (body !== undefined) requestHeaders["content-type"] = "application/json";

  const response = await impit.fetch(makeUrl(path, params), {
    method,
    headers: requestHeaders,
    body,
    signal: AbortSignal.timeout(30000),
  });
  const raw = await response.text();
  let data = raw;
  try {
    data = raw ? JSON.parse(raw) : null;
  } catch {
    // Keep non-JSON error responses as text.
  }
  if (response.status >= 400) {
    const error = new Error(`HTTP ${response.status}`);
    error.status = response.status;
    error.body = typeof data === "string" ? data.slice(0, 500) : data;
    throw error;
  }
  return data;
}

async function ensureAuthKey(message) {
  if (authKey) return authKey;
  const data = await rawRequest({
    ...message,
    method: "GET",
    path: "/v2/accounts/auth_key/",
    params: {},
    jsonBody: undefined,
    headers: { req_key: REQ_KEY },
  });
  authKey = data?.auth_key;
  if (!authKey) throw new Error("Blinkit did not issue an anonymous session key");
  return authKey;
}

async function handle(message) {
  const key = await ensureAuthKey(message);
  return rawRequest({ ...message, headers: { auth_key: key } });
}

const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
input.on("line", async (line) => {
  try {
    const message = JSON.parse(line);
    const data = await handle(message);
    process.stdout.write(JSON.stringify({ ok: true, data }) + "\n");
  } catch (error) {
    process.stdout.write(JSON.stringify({
      ok: false,
      status: error?.status ?? 0,
      error: error?.message ?? String(error),
      body: error?.body,
    }) + "\n");
  }
});
