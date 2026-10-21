// Local MCP server that connects Antigravity to the Genie One MCP server, signing requests with the Databricks CLI login.
// Antigravity starts it over stdio; each JSON-RPC message is forwarded to Genie One and the replies are written back.
// Tokens come from `databricks auth token` and never leave this process.
import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { delimiter, join } from "node:path";
import { createInterface } from "node:readline";
import { pathToFileURL } from "node:url";

const GENIE_ONE_PATH = "/ai-gateway/mcp-services/system.ai.genie_one_mcp";
const REFRESH_MARGIN_MS = 60_000;

export function genieUrl(host) {
  const base = /^https?:\/\//.test(host) ? host : `https://${host}`;
  return base.replace(/\/+$/, "") + GENIE_ONE_PATH;
}

// Antigravity can start this with a minimal PATH, so also look in the usual install folders.
export function cliCandidates(env) {
  const home = env.HOME || homedir();
  const onPath = (env.PATH || "").split(delimiter).filter(Boolean);
  const usual = ["/opt/homebrew/bin", "/usr/local/bin", join(home, ".local", "bin"), join(home, "bin")];
  return [...new Set([...onPath, ...usual].map((dir) => join(dir, "databricks")))];
}

class BridgeError extends Error {}

// Everything the bridge needs from Databricks comes from one CLI profile: the workspace and fresh tokens.
function databricksCli(env) {
  const profile = env.DATABRICKS_CONFIG_PROFILE || "DEFAULT";
  const loginHint = `Run: databricks auth login --profile ${profile}`;
  // Let the profile decide the workspace and credentials, not variables inherited from the agent.
  const { DATABRICKS_HOST, DATABRICKS_TOKEN, ...cliEnv } = env;
  let cached = null;

  const run = (args, failure) =>
    new Promise((resolve, reject) => {
      const cli = env.DATABRICKS_CLI || cliCandidates(env).find((path) => existsSync(path));
      if (!cli) return reject(new BridgeError(`The Databricks CLI wasn't found. Install it, then run: databricks auth login --profile ${profile}`));
      execFile(cli, [...args, "--profile", profile, "--output", "json"], { env: cliEnv, timeout: 30_000 }, (error, stdout) => {
        if (error) return reject(new BridgeError(`${failure} ${loginHint}`));
        try {
          resolve(JSON.parse(stdout));
        } catch {
          reject(new BridgeError(`The Databricks CLI returned an unexpected response. ${loginHint}`));
        }
      });
    });

  return {
    loginHint,
    async workspaceUrl() {
      const { details } = await run(["auth", "describe"], `Couldn't find the Databricks workspace for CLI profile ${profile}.`);
      if (!details?.host) throw new BridgeError(`CLI profile ${profile} has no workspace URL. ${loginHint}`);
      return genieUrl(details.host);
    },
    async get() {
      if (!cached || Date.now() >= cached.refreshAt) {
        const { access_token: token, expires_in: expiresIn, expiry } = await run(["auth", "token"], "Couldn't get a Databricks token from the CLI.");
        const lifetime = expiresIn ? expiresIn * 1000 : Date.parse(expiry) - Date.now();
        cached = { token, refreshAt: Date.now() + (Number.isFinite(lifetime) ? lifetime - REFRESH_MARGIN_MS : 5 * 60_000) };
      }
      return cached.token;
    },
    invalidate() {
      cached = null;
    },
  };
}

function parseEventStream(text) {
  return text
    .split(/\r?\n\r?\n/)
    .map((event) => event.split(/\r?\n/).filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n"))
    .filter((data) => data.trim())
    .map((data) => JSON.parse(data));
}

async function forward(message, { url, tokens, session }) {
  for (let attempt = 0; ; attempt++) {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${await tokens.get()}`,
        "Content-Type": "application/json",
        Accept: "application/json, text/event-stream",
        ...(session.id ? { "Mcp-Session-Id": session.id } : {}),
      },
      body: JSON.stringify(message),
    });
    const sessionId = response.headers.get("mcp-session-id");
    if (sessionId) session.id = sessionId;
    const text = await response.text();
    if (response.status === 401 && attempt === 0) {
      tokens.invalidate();
      continue;
    }
    if (response.status === 401 || response.status === 403) {
      throw new BridgeError(`Genie rejected the Databricks sign-in (HTTP ${response.status}). ${tokens.loginHint}`);
    }
    if (!response.ok) throw new BridgeError(`Genie returned HTTP ${response.status}: ${text.slice(0, 200)}`);
    if (!text.trim()) return [];
    if ((response.headers.get("content-type") || "").includes("text/event-stream")) return parseEventStream(text);
    const parsed = JSON.parse(text);
    return Array.isArray(parsed) ? parsed : [parsed];
  }
}

function write(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

async function handle(line, context) {
  let message;
  try {
    message = JSON.parse(line);
  } catch {
    return write({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } });
  }
  try {
    context.url ||= await context.tokens.workspaceUrl();
    for (const reply of await forward(message, context)) write(reply);
  } catch (error) {
    const text = error instanceof BridgeError ? error.message : `Genie bridge error: ${error.message}`;
    if (message.id !== undefined && message.id !== null) write({ jsonrpc: "2.0", id: message.id, error: { code: -32000, message: text } });
    else process.stderr.write(`${text}\n`);
  }
}

function main() {
  const env = process.env;
  const context = {
    url: env.GENIE_MCP_URL || (env.DATABRICKS_HOST ? genieUrl(env.DATABRICKS_HOST) : null),
    tokens: databricksCli(env),
    session: {},
  };
  // Handle messages one at a time so replies and the session id stay in order.
  let queue = Promise.resolve();
  const input = createInterface({ input: process.stdin });
  input.on("line", (line) => {
    if (line.trim()) queue = queue.then(() => handle(line, context));
  });
  input.on("close", () => queue.then(() => process.exit(0)));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main();
