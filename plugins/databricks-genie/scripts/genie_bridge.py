#!/usr/bin/env python3
"""Local MCP server that connects Antigravity to the Genie One MCP server, signing requests with the Databricks CLI login.

Antigravity starts it over stdio; each JSON-RPC message is forwarded to Genie One and the replies are written back.
Tokens come from `databricks auth token` and never leave this process. Standard library only, so it runs on the
python3 every developer machine already has (3.9 or later).
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

GENIE_ONE_PATH = "/ai-gateway/mcp-services/system.ai.genie_one_mcp"
REFRESH_MARGIN_SECONDS = 60


def genie_url(host):
    base = host if host.startswith(("http://", "https://")) else "https://" + host
    return base.rstrip("/") + GENIE_ONE_PATH


def cli_candidates(env):
    """Antigravity can start this with a minimal PATH, so also look in the usual install folders."""
    home = env.get("HOME") or os.path.expanduser("~")
    folders = [folder for folder in env.get("PATH", "").split(os.pathsep) if folder]
    folders += ["/opt/homebrew/bin", "/usr/local/bin", os.path.join(home, ".local", "bin"), os.path.join(home, "bin")]
    candidates = []
    for folder in folders:
        path = os.path.join(folder, "databricks")
        if path not in candidates:
            candidates.append(path)
    return candidates


class BridgeError(Exception):
    pass


class DatabricksCli:
    """Everything the bridge needs from Databricks comes from one CLI profile: the workspace and fresh tokens."""

    def __init__(self, env):
        self.env = env
        self.profile = env.get("DATABRICKS_CONFIG_PROFILE") or "DEFAULT"
        self.login_hint = "Run: databricks auth login --profile " + self.profile
        # Let the profile decide the workspace and credentials, not variables inherited from the agent.
        self.cli_env = {key: value for key, value in env.items() if key not in ("DATABRICKS_HOST", "DATABRICKS_TOKEN")}
        self.cached = None  # (token, time to refresh it)

    def run(self, args, failure):
        cli = self.env.get("DATABRICKS_CLI") or next((path for path in cli_candidates(self.env) if os.path.exists(path)), None)
        if not cli:
            raise BridgeError("The Databricks CLI wasn't found. Install it, then run: databricks auth login --profile " + self.profile)
        try:
            result = subprocess.run(
                [cli, *args, "--profile", self.profile, "--output", "json"],
                env=self.cli_env, capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise BridgeError(failure + " " + self.login_hint)
        if result.returncode != 0:
            raise BridgeError(failure + " " + self.login_hint)
        try:
            return json.loads(result.stdout)
        except ValueError:
            raise BridgeError("The Databricks CLI returned an unexpected response. " + self.login_hint)

    def workspace_url(self):
        reply = self.run(["auth", "describe"], "Couldn't find the Databricks workspace for CLI profile " + self.profile + ".")
        host = (reply.get("details") or {}).get("host")
        if not host:
            raise BridgeError("CLI profile " + self.profile + " has no workspace URL. " + self.login_hint)
        return genie_url(host)

    def token(self):
        if self.cached is None or time.time() >= self.cached[1]:
            reply = self.run(["auth", "token"], "Couldn't get a Databricks token from the CLI.")
            lifetime = reply.get("expires_in")
            if not lifetime:
                try:
                    lifetime = datetime.fromisoformat(reply["expiry"]).timestamp() - time.time()
                except (KeyError, TypeError, ValueError):
                    lifetime = None
            refresh_at = time.time() + (float(lifetime) - REFRESH_MARGIN_SECONDS if lifetime is not None else 300)
            self.cached = (reply["access_token"], refresh_at)
        return self.cached[0]

    def invalidate(self):
        self.cached = None


def parse_event_stream(text):
    messages = []
    for event in text.replace("\r\n", "\n").split("\n\n"):
        data = "\n".join(line[5:].lstrip() for line in event.split("\n") if line.startswith("data:"))
        if data.strip():
            messages.append(json.loads(data))
    return messages


def forward(message, context):
    for attempt in (0, 1):
        headers = {
            "Authorization": "Bearer " + context["cli"].token(),
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if context["session_id"]:
            headers["Mcp-Session-Id"] = context["session_id"]
        request = urllib.request.Request(context["url"], data=json.dumps(message).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                status, response_headers, text = response.status, response.headers, response.read().decode()
        except urllib.error.HTTPError as error:
            status, response_headers, text = error.code, error.headers, error.read().decode(errors="replace")
        except urllib.error.URLError as error:
            raise BridgeError("Couldn't reach Genie: {}".format(error.reason))
        if response_headers.get("Mcp-Session-Id"):
            context["session_id"] = response_headers["Mcp-Session-Id"]
        if status == 401 and attempt == 0:
            context["cli"].invalidate()
            continue
        if status in (401, 403):
            raise BridgeError("Genie rejected the Databricks sign-in (HTTP {}). {}".format(status, context["cli"].login_hint))
        if status >= 400:
            raise BridgeError("Genie returned HTTP {}: {}".format(status, text[:200]))
        if not text.strip():
            return []
        if "text/event-stream" in (response_headers.get("Content-Type") or ""):
            return parse_event_stream(text)
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else [parsed]


def write(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def handle(line, context):
    try:
        message = json.loads(line)
    except ValueError:
        return write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
    try:
        if not context["url"]:
            context["url"] = context["cli"].workspace_url()
        for reply in forward(message, context):
            write(reply)
    except Exception as error:  # every failure becomes a JSON-RPC error for the agent, never a crash
        text = str(error) if isinstance(error, BridgeError) else "Genie bridge error: {}".format(error)
        if isinstance(message, dict) and message.get("id") is not None:
            write({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32000, "message": text}})
        else:
            sys.stderr.write(text + "\n")


def main():
    env = dict(os.environ)
    host = env.get("DATABRICKS_HOST")
    context = {"url": env.get("GENIE_MCP_URL") or (genie_url(host) if host else None), "cli": DatabricksCli(env), "session_id": None}
    # Handle messages one at a time so replies and the session id stay in order.
    for line in sys.stdin:
        if line.strip():
            handle(line, context)


if __name__ == "__main__":
    main()
