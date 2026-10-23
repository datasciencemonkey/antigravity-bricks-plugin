"""The Genie bridge: a local stdio MCP server that forwards to the Genie One MCP, signed with the Databricks CLI login.

Runs the real bridge under Python against a stand-in `databricks` CLI and a stand-in Genie MCP server.
"""
import ast
import importlib.util
import io
import json
import os
import queue
import re
import shutil
import signal
import socket
import stat
import subprocess
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

BRIDGE = Path(__file__).resolve().parent.parent / "plugins" / "databricks-genie" / "scripts" / "genie_bridge.py"
# Run the bridge under the oldest Python a developer is likely to have: Apple's /usr/bin/python3 is 3.9.
PYTHON = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else "python3"
GENIE_ONE_PATH = "/ai-gateway/mcp-services/system.ai.genie_one_mcp"
LOOPBACK = "http://127.0.0.1:9/mcp"  # the only kind of GENIE_MCP_URL the bridge accepts: a test server on this machine
INITIALIZE ={"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}}
TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
LIST_CHANGED = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
GENIE_TOOLS = ["genie_ask", "genie_poll_response", "genie_get_query_result", "genie_cancel_response"]
BRIDGE_TOOLS = ["genie_connection", "genie_setup"]
POLL = {"conversation_id": "c-1", "response_id": "r-1"}
JSON = {"Content-Type": "application/json"}


def workspace(name="company-workspace", domain="cloud.databricks.com"):
    """A made-up workspace URL. The repo's hostname check allows the default one; any other is only built here."""
    return f"https://{name}.{domain}"


WORKSPACE = workspace()


def call(name, arguments=None, number=3):
    return {"jsonrpc": "2.0", "id": number, "method": "tools/call", "params": {"name": name, "arguments": arguments or {}}}


def text_of(reply):
    return "\n".join(item["text"] for item in reply["result"]["content"] if item["type"] == "text")


def report_of(reply):
    """The JSON report that genie_connection and genie_setup return."""
    return json.loads(text_of(reply))


def tool_reply(text):
    """A Genie reply to a tool call that carries one text item."""
    return lambda request: (200, JSON, json.dumps({"jsonrpc": "2.0", "id": request["body"].get("id"),
                                                   "result": {"content": [{"type": "text", "text": text}]}}))


class FakeGenie:
    """Records each request; `reply(request) -> (status, headers, body)` decides the response."""

    def __init__(self):
        self.requests = []
        self.reply = lambda request: (200, JSON, json.dumps({"jsonrpc": "2.0", "id": request["body"].get("id"), "result": {"ok": True}}))
        self.trickle = None  # (method, seconds): send the reply to that method one byte at a time
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                request = {"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body}
                fake.requests.append(request)
                status, headers, payload = fake.reply(request)
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                data = payload.encode()
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    if fake.trickle and body.get("method") == fake.trickle[0]:
                        for byte in data:
                            self.wfile.write(bytes([byte]))
                            time.sleep(fake.trickle[1])
                    else:
                        self.wfile.write(data)
                except OSError:  # the bridge gave up and hung up
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/mcp"
        self.base = self.url.rsplit("/", 1)[0]
        self.host = self.url.split("/")[2]

    def methods(self):
        return [request["body"].get("method") for request in self.requests]


@pytest.fixture
def genie():
    fake = FakeGenie()
    yield fake
    fake.server.shutdown()


# A stand-in `databricks` CLI that keeps its profiles in the config file, $DATABRICKS_CONFIG_FILE (else ~/.databrickscfg),
# and reads that file the way CLI v1.22.1 does, which the tests below check against the real CLI wherever it's installed.
# `auth profiles` lists only the sections that have a host or an account_id, so a section with neither (an Azure service
# principal that sets azure_workspace_resource_id, or the empty [DEFAULT] header that `databricks configure` writes) is in
# the file and never listed; keys before the first header belong to DEFAULT; and a missing or unreadable file is an error,
# while an empty one lists nothing. `auth token` works only for U2M (databricks-cli) profiles. `auth login` rewrites the
# section it signs in with a host and databricks-cli, which keeps none of its other keys: that is how a client secret is
# lost. It accepts only the flags the bridge should pass, records each call in $FAKE_CLI_CALLS, and the names (never the
# values) of the DATABRICKS_* variables it was started with in $FAKE_CLI_CALLS.env, and hands out the comma-separated
# $FAKE_TOKENS in order. FAKE_CLI_FAIL makes `auth token` fail as an expired sign-in does, FAKE_PROFILES_FAIL breaks
# `auth profiles`, FAKE_LOGIN_SECONDS and FAKE_LOGIN_FAIL shape `auth login`, and FAKE_CLI_CONFIG names a config file to read
# instead of the one in DATABRICKS_CONFIG_FILE, as the real CLI does on Windows, where it looks under USERPROFILE, wherever
# HOME points.
FAKE_CLI = r"""#!/bin/sh
echo "$@" >> "$FAKE_CLI_CALLS"
env | sed -n 's/^\(DATABRICKS_[A-Za-z0-9_]*\)=.*/\1/p' >> "$FAKE_CLI_CALLS.env"
fail() { echo "Error: $*" >&2; exit 1; }
config="${FAKE_CLI_CONFIG:-${DATABRICKS_CONFIG_FILE:-~/.databrickscfg}}"
case "$config" in "~"*) config="$HOME${config#"~"}" ;; esac
need_config() { [ -f "$config" ] || fail "cannot parse config file: no configuration file found at $config; please create one by running 'databricks auth login'"; }
# The config file as section<TAB>key<TAB>value lines, read like the real CLI's INI parser: keys before the first header
# belong to DEFAULT, # and ; start a comment, the first = or : ends a key, and a name between [ and ] keeps its spaces.
ini() {
  awk '
    BEGIN { section = "DEFAULT" }
    { sub(/\r$/, ""); line = $0; sub(/^[ \t]+/, "", line); sub(/[ \t]+$/, "", line) }
    line == "" || line ~ /^[#;]/ { next }
    line ~ /^\[/ { section = substr(line, 2); sub(/\].*$/, "", section); next }
    {
      cut = index(line, "="); colon = index(line, ":")
      if (cut == 0 || (colon > 0 && colon < cut)) cut = colon
      key = substr(line, 1, cut - 1); value = substr(line, cut + 1)
      sub(/[ \t]+$/, "", key); sub(/^[ \t]+/, "", value)
      printf "%s\t%s\t%s\n", section, key, value
    }
  ' "$config"
}
field() { ini | awk -F'\t' -v s="$1" -v k="$2" '$1 == s && $2 == k { value = $3 } END { print value }'; }
command="$1 $2"
shift 2
host="" profile="" output="" timeout="" skip=""
while [ $# -gt 0 ]; do
  case "$1" in
    --host|--profile|--output|--timeout)
      [ $# -ge 2 ] || fail "flag needs an argument: $1"
      case "$1" in
        --host) host="$2" ;;
        --profile) profile="$2" ;;
        --output) output="$2" ;;
        --timeout) timeout="$2" ;;
      esac
      shift 2
      ;;
    --skip-validate) skip=1; shift ;;
    *) fail "unknown flag: $1" ;;
  esac
done
case "$command" in
"auth profiles")
  [ -n "$skip" ] && [ "$output" = json ] && [ -z "$host$profile$timeout" ] || fail "unexpected flags for $command"
  [ -z "$FAKE_PROFILES_FAIL" ] || fail "cannot parse config file"
  need_config
  ini | awk -F'\t' '
    !($1 in known) { known[$1] = 1; names[++count] = $1 }
    $2 == "host" { host[$1] = $3 }
    $2 == "account_id" { account[$1] = $3 }
    $2 == "auth_type" { auth[$1] = $3 }
    END {
      listed = 0
      for (i = 1; i <= count; i++) {
        name = names[i]
        if (host[name] == "" && account[name] == "") continue
        printf "%s{\"name\":\"%s\",", (listed ? "," : "{\"profiles\":["), name
        if (host[name] != "") printf "\"host\":\"%s\",", host[name]
        printf "\"auth_type\":\"%s\",\"valid\":false}", auth[name]
        listed++
      }
      print (listed ? "]}" : "{\"profiles\":null}")
    }'
  ;;
"auth login")
  [ -n "$host" ] && [ -n "$profile" ] && [ -z "$output$skip" ] || fail "unexpected flags for $command"
  case "$timeout" in [1-9]*m) ;; *) fail "expected --timeout <minutes>m, got: $timeout" ;; esac
  cat > /dev/null  # a sign-in that shared the bridge's stdin would wait here, swallowing Antigravity's messages
  echo "Opening the browser to sign in"  # output that must never reach the MCP stream
  echo "Waiting for the browser" >&2
  sleep "${FAKE_LOGIN_SECONDS:-0}"
  [ -z "$FAKE_LOGIN_FAIL" ] || fail "the sign-in was cancelled"
  if [ -f "$config" ]; then
    awk -v target="$profile" '
      BEGIN { skipping = (target == "DEFAULT") }
      /^[ \t]*\[/ { name = $0; sub(/^[ \t]*\[/, "", name); sub(/\].*$/, "", name); skipping = (name == target); if (skipping) next }
      !skipping
    ' "$config" > "$config.new"
  else
    : > "$config.new"
  fi
  printf '[%s]\nhost = %s\nauth_type = databricks-cli\n\n' "$profile" "$host" >> "$config.new"
  mv "$config.new" "$config"
  echo "Profile $profile was successfully saved"
  ;;
"auth token")
  [ -n "$profile" ] && [ "$output" = json ] && [ -z "$host$timeout$skip" ] || fail "unexpected flags for $command"
  need_config
  [ -n "$(field "$profile" host)" ] || fail "has no $profile profile configured"
  [ "$(field "$profile" auth_type)" = databricks-cli ] || fail "auth token only works with U2M (databricks-cli) profiles"
  [ -z "$FAKE_CLI_FAIL" ] || fail "refresh token is invalid"
  echo >> "$FAKE_CLI_CALLS.tokens"
  n=$(grep -c "" "$FAKE_CLI_CALLS.tokens")
  token=$(echo "$FAKE_TOKENS" | cut -d, -f"$n")
  printf '{"access_token":"%s","token_type":"Bearer","expires_in":%s}' "$token" "${FAKE_EXPIRES_IN:-3600}"
  ;;
*)
  fail "the fake CLI doesn't know: $command"
  ;;
esac
"""


class CliScript:
    """The stand-in CLI on disk, seeded with signed-in DEMO and DEFAULT profiles for the placeholder workspace."""

    def __init__(self, folder):
        self.path = folder / "databricks"
        self.path.write_text(FAKE_CLI)
        self.path.chmod(0o755)
        self.log = folder / "cli-calls"
        self.config = folder / "databrickscfg"  # the CLI's config file, which the bridge also reads
        self.home = folder / "home"  # the bridge's home folder
        self.records = self.home / ".cache" / "antigravity-databricks-genie"  # where it records a sign-in in progress
        self.tmp = folder / "tmp"  # the bridge's temp folder, which must stay empty
        self.home.mkdir()
        self.tmp.mkdir()
        self.set_profiles({"DEMO": WORKSPACE, "DEFAULT": WORKSPACE})

    def set_profiles(self, profiles, extra=""):
        """Write the config file. Each profile maps to its host (a U2M profile) or to (host, auth type); extra is raw text after
        them, such as a section the CLI doesn't list."""
        sections = []
        for name, value in profiles.items():
            host, auth_type = value if isinstance(value, tuple) else (value, "databricks-cli")
            sections.append(f"[{name}]\nhost = {host}\n" + (f"auth_type = {auth_type}\n" if auth_type else "") + "\n")
        self.config.write_text("".join(sections) + extra)

    def set_config(self, text):
        """Put raw text in the config file."""
        self.config.write_text(text)

    def variables(self):
        """The names of the DATABRICKS_* variables the CLI was started with, on any call."""
        names = self.log.with_name(self.log.name + ".env")
        return set(names.read_text().split()) if names.exists() else set()

    def login_records(self):
        return sorted(self.records.glob("databricks-genie-login-*.json"))

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def logins(self):
        return [line for line in self.calls() if line.startswith("auth login")]

    def env(self):
        return {"DATABRICKS_CLI": str(self.path), "FAKE_CLI_CALLS": str(self.log),
                "DATABRICKS_CONFIG_FILE": str(self.config), "HOME": str(self.home), "TMPDIR": str(self.tmp)}


@pytest.fixture
def cli(tmp_path):
    return CliScript(tmp_path)


def cli_calls(cli):
    return cli.calls()


def without_databricks_variables(environ):
    """The environment with every DATABRICKS_* variable removed, so a variable of the developer's shell can't reach a test."""
    return {key: value for key, value in environ.items() if not key.startswith("DATABRICKS_")}


class Bridge:
    def __init__(self, genie, cli, new_session=False, **env):
        self.process = subprocess.Popen(
            [PYTHON, str(BRIDGE)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=new_session,
            env={**without_databricks_variables(os.environ), "GENIE_MCP_URL": genie.url, "DATABRICKS_CONFIG_PROFILE": "DEMO",
                 "FAKE_TOKENS": "tok-1,tok-2", **cli.env(), **env},
        )
        self.lines = queue.Queue()
        self.output = []  # every line the bridge wrote to stdout
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.process.stdout:
            self.output.append(line)
            self.lines.put(line)

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def reply(self, timeout=10):
        return json.loads(self.lines.get(timeout=timeout))

    def connection(self, number):
        """Call genie_connection; return the notifications written before its reply, and its report."""
        self.send(call("genie_connection", number=number))
        notifications = []
        while True:
            message = self.reply()
            if "id" not in message:
                notifications.append(message)
                continue
            return notifications, report_of(message)

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=10)
        return self.process.stderr.read()


@pytest.fixture
def start(genie, cli):
    bridges = []

    def _start(new_session=False, **env):
        bridges.append(Bridge(genie, cli, new_session=new_session, **env))
        return bridges[-1]

    yield _start
    for bridge in bridges:
        if bridge.process.poll() is None:
            bridge.process.kill()


def wait_for(condition, timeout=10):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting"
        time.sleep(0.05)


def wait_for_sign_in_to_end(bridge, first_number=100):
    """Poll genie_connection until the sign-in is over; return the notifications seen and the last report."""
    seen = []
    for number in range(first_number, first_number + 200):
        notifications, report = bridge.connection(number)
        seen += notifications
        if report["status"] != "login_in_progress":
            return seen, report
        time.sleep(0.05)
    raise AssertionError("the sign-in never ended")


# Forwarding to Genie One, signed with the CLI's token


def test_forwards_a_request_signed_with_the_cli_token_and_returns_the_reply(start, genie, cli):
    bridge = start()
    bridge.send(INITIALIZE)
    bridge.reply()
    bridge.send(call("genie_poll_response", POLL))

    assert bridge.reply() == {"jsonrpc": "2.0", "id": 3, "result": {"ok": True}}
    assert genie.requests[-1]["body"] == call("genie_poll_response", POLL)
    assert genie.requests[-1]["headers"]["authorization"] == "Bearer tok-1"
    assert cli_calls(cli) == ["auth token --profile DEMO --output json"]


def test_opens_the_genie_session_only_when_a_request_needs_genie(start, genie):
    bridge = start()
    bridge.send(INITIALIZE)
    bridge.reply()
    bridge.send(INITIALIZED)
    bridge.send(call("genie_connection"))
    bridge.reply()

    assert genie.requests == []

    bridge.send(TOOLS_LIST)
    bridge.reply()

    assert genie.methods() == ["initialize", "notifications/initialized", "tools/list"]
    assert genie.requests[0]["body"]["params"] == INITIALIZE["params"]  # Genie sees Antigravity's own initialize


def test_sends_the_session_id_from_initialize_on_later_requests(start, genie):
    genie.reply = lambda request: (200, {**JSON, "Mcp-Session-Id": "session-7"},
                                   json.dumps({"jsonrpc": "2.0", "id": request["body"].get("id"), "result": {}}))
    bridge = start()
    bridge.send(TOOLS_LIST)
    bridge.reply()
    bridge.send(call("genie_poll_response", POLL))
    bridge.reply()

    assert "mcp-session-id" not in genie.requests[0]["headers"]
    assert [request["headers"].get("mcp-session-id") for request in genie.requests[1:]] == ["session-7"] * 3


def test_starts_a_new_genie_session_when_genie_ends_the_old_one(start, genie):
    sessions = iter(["session-1", "session-2"])

    def reply(request):
        if request["body"].get("method") == "initialize":
            return 200, {**JSON, "Mcp-Session-Id": next(sessions)}, json.dumps({"jsonrpc": "2.0", "id": request["body"]["id"], "result": {}})
        if request["body"].get("method") == "tools/call" and request["headers"].get("mcp-session-id") == "session-1":
            return 404, {"Content-Type": "text/plain"}, "session not found"
        return 200, JSON, json.dumps({"jsonrpc": "2.0", "id": request["body"].get("id"), "result": {"ok": True}})

    genie.reply = reply
    bridge = start()
    bridge.send(TOOLS_LIST)
    bridge.reply()
    bridge.send(call("genie_poll_response", POLL))

    assert bridge.reply() == {"jsonrpc": "2.0", "id": 3, "result": {"ok": True}}
    assert genie.methods() == ["initialize", "notifications/initialized", "tools/list", "tools/call",
                               "initialize", "notifications/initialized", "tools/call"]
    assert genie.requests[-1]["headers"]["mcp-session-id"] == "session-2"


def test_forwards_notifications_without_writing_a_reply(start, genie):
    genie.reply = lambda request: ((202, {}, "") if "id" not in request["body"] else
                                   (200, JSON, json.dumps({"jsonrpc": "2.0", "id": request["body"]["id"], "result": {}})))
    cancelled = {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 2}}
    bridge = start()
    bridge.send(TOOLS_LIST)
    bridge.reply()
    bridge.send(cancelled)
    bridge.send({**TOOLS_LIST, "id": 4})

    assert bridge.reply()["id"] == 4
    assert cancelled in [request["body"] for request in genie.requests]


def test_unwraps_event_stream_replies(start, genie):
    def reply(request):
        message = {"jsonrpc": "2.0", "id": request["body"].get("id"), "result": {"done": True}}
        return 200, {"Content-Type": "text/event-stream"}, f"event: message\ndata: {json.dumps(message)}\n\n"

    genie.reply = reply
    bridge = start()
    bridge.send(call("genie_poll_response", POLL))

    assert bridge.reply() == {"jsonrpc": "2.0", "id": 3, "result": {"done": True}}


def test_reuses_the_token_until_it_is_about_to_expire(start, cli):
    bridge = start()
    bridge.send(TOOLS_LIST)
    bridge.reply()
    bridge.send({**TOOLS_LIST, "id": 4})
    bridge.reply()

    assert len(cli_calls(cli)) == 1


def test_gets_a_new_token_and_retries_once_when_the_token_is_rejected(start, genie, cli):
    def reply(request):
        if request["headers"]["authorization"] == "Bearer tok-1":
            return 401, JSON, '{"error":"invalid token"}'
        return 200, JSON, json.dumps({"jsonrpc": "2.0", "id": request["body"].get("id"), "result": {}})

    genie.reply = reply
    bridge = start()
    bridge.send(call("genie_poll_response", POLL))

    assert bridge.reply() == {"jsonrpc": "2.0", "id": 3, "result": {}}
    # The rejected initialize is sent again with the new token, and the rest of the session uses it too.
    assert [r["headers"]["authorization"] for r in genie.requests] == ["Bearer tok-1"] + ["Bearer tok-2"] * 3
    assert len(cli_calls(cli)) == 2


def test_upstream_http_errors_on_tool_calls_become_tool_errors_the_agent_can_read(start, genie):
    genie.reply = lambda request: (500, {"Content-Type": "text/plain"}, "boom")
    bridge = start()
    bridge.send(call("genie_poll_response", POLL))
    reply = bridge.reply()

    assert reply["id"] == 3 and reply["result"]["isError"] is True
    assert "HTTP 500" in text_of(reply)


def test_upstream_http_errors_become_json_rpc_errors(start, genie):
    genie.reply = lambda request: (500, {"Content-Type": "text/plain"}, "boom")
    bridge = start()
    bridge.send({"jsonrpc": "2.0", "id": 5, "method": "resources/list"})
    reply = bridge.reply()

    assert reply["id"] == 5
    assert "HTTP 500" in reply["error"]["message"]


def bridge_eval(expression):
    """Evaluate `expression` with the bridge module loaded as `m`, under the same Python the bridge runs on."""
    code = ("import importlib.util, json, sys; spec = importlib.util.spec_from_file_location('bridge', sys.argv[1]); "
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print(json.dumps(" + expression + "))")
    return json.loads(subprocess.run([PYTHON, "-c", code, str(BRIDGE)], capture_output=True, text=True, check=True).stdout)


@pytest.mark.parametrize("host", ["workspace.example.test", "https://workspace.example.test", "https://workspace.example.test/"])
def test_the_default_url_is_the_genie_one_mcp_on_the_configured_host(host):
    assert bridge_eval(f"m.genie_url({json.dumps(host)})") == \
        "https://workspace.example.test/ai-gateway/mcp-services/system.ai.genie_one_mcp"


def test_looks_for_the_cli_in_the_usual_install_folders_when_path_is_minimal():
    candidates = bridge_eval('m.cli_candidates({"PATH": "/usr/bin", "HOME": "/home/me"})')

    assert candidates[0] == "/usr/bin/databricks"
    for folder in ("/opt/homebrew/bin", "/usr/local/bin", "/home/me/.local/bin"):
        assert f"{folder}/databricks" in candidates


def test_reads_the_workspace_from_the_cli_profile_when_no_host_is_given(start, genie, cli):
    cli.set_profiles({"DEMO": genie.base})
    bridge = start(GENIE_MCP_URL="")
    bridge.send(TOOLS_LIST)
    bridge.reply()

    assert genie.requests[0]["path"] == GENIE_ONE_PATH
    # The profile list is a local read of the CLI's config, so finding the workspace needs no network call.
    assert "auth profiles --skip-validate --output json" in cli_calls(cli)


def test_uses_the_default_cli_profile_unless_one_is_set(start, cli):
    bridge = start(DATABRICKS_CONFIG_PROFILE="")
    bridge.send(TOOLS_LIST)
    bridge.reply()

    assert cli_calls(cli) == ["auth token --profile DEFAULT --output json"]


def test_a_failed_workspace_lookup_becomes_an_error_that_says_how_to_fix_it(start, genie):
    bridge = start(GENIE_MCP_URL="", FAKE_PROFILES_FAIL="1")
    bridge.send(call("genie_ask", {"question": "Revenue last quarter?"}))
    reply = bridge.reply()

    assert reply["id"] == 3 and reply["result"]["isError"] is True
    assert "databricks auth profiles" in text_of(reply)
    assert genie.requests == []


def test_asks_the_cli_for_a_new_token_shortly_before_the_old_one_expires(start, cli):
    # The CLI's tokens last an hour and the bridge renews a minute early, so a 61-second token is stale after a second.
    bridge = start(FAKE_EXPIRES_IN="61")
    bridge.send(TOOLS_LIST)
    bridge.reply()
    time.sleep(1.5)
    bridge.send({**TOOLS_LIST, "id": 3})
    bridge.reply()

    assert cli_calls(cli) == ["auth token --profile DEMO --output json"] * 2


# Starting with no CLI profile: the bridge answers initialize and tools/list itself


@pytest.mark.parametrize("asked, answered", [("2025-06-18", "2025-06-18"), ("2025-03-26", "2025-03-26"),
                                             ("2024-11-05", "2024-11-05"), ("2099-01-01", "2025-06-18")])
def test_answers_initialize_itself_so_it_starts_with_no_profile_configured(start, genie, cli, asked, answered):
    cli.set_profiles({})
    bridge = start(DATABRICKS_CONFIG_PROFILE="NEW")
    bridge.send({**INITIALIZE, "params": {**INITIALIZE["params"], "protocolVersion": asked}})
    result = bridge.reply()["result"]

    assert result["protocolVersion"] == answered  # a version the bridge knows, never just an echo
    assert result["capabilities"]["tools"] == {"listChanged": True}
    assert result["serverInfo"]["name"] == "databricks-genie-bridge"
    assert "genie_connection" in result["instructions"]
    assert genie.requests == []


def test_lists_genies_tools_and_its_own_with_no_profile_configured(start, genie, cli):
    cli.set_profiles({})
    bridge = start(DATABRICKS_CONFIG_PROFILE="NEW")
    bridge.send(INITIALIZE)
    bridge.reply()
    bridge.send(INITIALIZED)
    bridge.send(TOOLS_LIST)
    tools = bridge.reply()["result"]["tools"]

    assert [tool["name"] for tool in tools] == GENIE_TOOLS + BRIDGE_TOOLS
    assert all(tool["description"] and tool["inputSchema"]["type"] == "object" for tool in tools)
    assert genie.requests == []  # not even Antigravity's notifications/initialized


def test_answers_ping_itself(start, genie, cli):
    cli.set_profiles({})
    bridge = start(DATABRICKS_CONFIG_PROFILE="NEW")
    bridge.send({"jsonrpc": "2.0", "id": 9, "method": "ping"})

    assert bridge.reply() == {"jsonrpc": "2.0", "id": 9, "result": {}}
    assert genie.requests == []


def test_once_connected_lists_genies_own_tools_plus_the_bridges(start, genie):
    live = [{"name": "genie_ask", "description": "Genie One's live description", "inputSchema": {"type": "object"}}]
    genie.reply = lambda request: (200, JSON, json.dumps({"jsonrpc": "2.0", "id": request["body"].get("id"),
                                                          "result": {"tools": live}}))
    bridge = start()
    bridge.send(TOOLS_LIST)
    tools = bridge.reply()["result"]["tools"]

    assert [tool["name"] for tool in tools] == ["genie_ask"] + BRIDGE_TOOLS
    assert tools[0]["description"] == "Genie One's live description"


def test_still_lists_the_tools_when_genie_cannot_be_reached(start, genie):
    genie.reply = lambda request: (403, {"Content-Type": "text/plain"}, "Forbidden")
    bridge = start()
    bridge.send(TOOLS_LIST)

    assert [tool["name"] for tool in bridge.reply()["result"]["tools"]] == GENIE_TOOLS + BRIDGE_TOOLS


# genie_connection: the status, the profile and the host, answered by the bridge


def test_genie_connection_reports_ready_with_the_profile_and_the_host(start, genie):
    bridge = start()
    bridge.send(call("genie_connection"))
    report = report_of(bridge.reply())

    assert report["status"] == "ready"
    assert report["profile"] == "DEMO"
    assert report["host"] == genie.host
    assert f"Asking Genie in {genie.host} (Databricks CLI profile DEMO)" in report["next_step"]


@pytest.mark.parametrize("where", ["GENIE_MCP_URL", "profile", "DATABRICKS_HOST", "a GENIE_MCP_URL that is not on this machine"])
def test_genie_connection_reports_the_host_the_bridge_will_actually_call(start, genie, cli, where):
    # The profile's host decides, because the token is the profile's. The one exception is a test server on this machine.
    env = {"GENIE_MCP_URL": ""}
    path = GENIE_ONE_PATH
    if where == "GENIE_MCP_URL":
        env["GENIE_MCP_URL"], path = genie.url, "/mcp"  # the profile stays at the default, a different workspace
    else:
        cli.set_profiles({"DEMO": genie.base})
    if where == "DATABRICKS_HOST":
        env["DATABRICKS_HOST"] = "https://unused.example.test"
    if where.startswith("a GENIE_MCP_URL"):
        env["GENIE_MCP_URL"] = "https://elsewhere.example.test/mcp"
    bridge = start(**env)
    _, report = bridge.connection(3)
    bridge.send(call("genie_poll_response", POLL, number=4))
    bridge.reply()

    assert report["host"] == genie.host
    assert {request["path"] for request in genie.requests} == {path}


def test_a_databricks_host_in_the_environment_never_decides_which_host_the_bridge_calls(start, genie, cli):
    shell_host = FakeGenie()
    try:
        cli.set_profiles({"DEMO": genie.base})
        bridge = start(GENIE_MCP_URL="", DATABRICKS_HOST=shell_host.base)
        bridge.send(call("genie_poll_response", POLL))
        bridge.reply()
    finally:
        shell_host.server.shutdown()

    assert shell_host.requests == []
    assert {request["headers"]["authorization"] for request in genie.requests} == {"Bearer tok-1"}


@pytest.mark.parametrize("url, accepted", [
    pytest.param("http://127.0.0.1:8080/mcp", True, id="127.0.0.1"),
    pytest.param("http://localhost:9/mcp", True, id="localhost"),
    pytest.param("http://[::1]:5000/mcp", True, id="::1"),
    pytest.param("https://127.0.0.2/mcp", True, id="another 127.x address"),
    pytest.param("https://elsewhere.example.test/mcp", False, id="another host"),
    pytest.param("http://127.0.0.1.example.test/mcp", False, id="a name that only starts like 127.0.0.1"),
    pytest.param("http://localhost.example.test/mcp", False, id="a name that only starts with localhost"),
    pytest.param("http://127.0.0.1@elsewhere.example.test/mcp", False, id="loopback as the user name"),
    pytest.param("http://elsewhere.example.test#@127.0.0.1/mcp", False, id="loopback in the fragment"),
    pytest.param("ftp://127.0.0.1/mcp", False, id="not http or https"),
    pytest.param("127.0.0.1:8080/mcp", False, id="no scheme"),
    pytest.param("not a url", False, id="not a URL"),
    pytest.param("", False, id="empty"),
])
def test_only_a_test_server_on_this_machine_is_accepted_as_the_genie_mcp_url(url, accepted):
    assert load_bridge().new_context({"GENIE_MCP_URL": url})["override"] == (url if accepted else None)


def test_a_genie_mcp_url_that_is_ignored_is_said_so_on_stderr_without_repeating_it(capsys):
    secret_url = "https://user:" + SECRET + "@elsewhere.example.test/mcp"

    assert load_bridge().new_context({"GENIE_MCP_URL": secret_url})["override"] is None
    err = capsys.readouterr().err

    assert "GENIE_MCP_URL" in err and "loopback" in err
    assert SECRET not in err and "elsewhere" not in err


def test_the_databricks_cli_gets_the_bridges_environment_unchanged(start, cli):
    # The CLI owns its config and its environment variables, where it keeps its U2M tokens (DATABRICKS_AUTH_STORAGE) among them,
    # so it gets them as a terminal would give them. DATABRICKS_CLI is how the tests point the bridge at the stand-in.
    exported = {"DATABRICKS_AUTH_STORAGE": "plaintext", "DATABRICKS_OAUTH_CALLBACK_PORT": "8020", "DATABRICKS_SOMETHING": "else",
                "DATABRICKS_HOST": "https://unused.example.test", "DATABRICKS_TOKEN": "made-up"}
    cli.set_profiles({})  # so that auth profiles, auth token and auth login all run
    bridge = start(DATABRICKS_CONFIG_PROFILE="NEW", **exported)
    bridge.send(call("genie_connection"))
    bridge.reply()
    bridge.send(call("genie_setup", {"workspace_url": WORKSPACE}, number=4))
    bridge.reply()
    wait_for(lambda: cli.logins())

    assert {line.split()[1] for line in cli.calls()} == {"profiles", "token", "login"}
    assert cli.variables() == {"DATABRICKS_CLI", "DATABRICKS_CONFIG_FILE", "DATABRICKS_CONFIG_PROFILE", *exported}


def test_the_cli_is_started_with_the_environment_itself_for_a_token_and_for_a_sign_in(monkeypatch):
    bridge = load_bridge()
    env = {"PATH": "/usr/bin", "DATABRICKS_CLI": "databricks", "DATABRICKS_AUTH_STORAGE": "plaintext",
           "DATABRICKS_HOST": "https://unused.example.test", "DATABRICKS_TOKEN": "made-up"}
    seen = []

    def run(command, **options):
        seen.append(options["env"])
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    def popen(command, **options):
        seen.append(options["env"])

    monkeypatch.setattr(bridge.subprocess, "run", run)
    monkeypatch.setattr(bridge.subprocess, "Popen", popen)
    cli = bridge.DatabricksCli(env)
    cli.run(["auth", "profiles"], "Couldn't read the profiles.")
    cli.login("https://unused.example.test")

    assert seen == [env, env]  # not a copy with anything taken out


def test_the_bridge_reads_no_databricks_variable_but_the_profile_the_config_file_and_the_path_of_the_cli():
    # The workspace comes from the CLI's profile listing alone, and the CLI's other variables are the CLI's business.
    names = set(re.findall(r"DATABRICKS_[A-Z0-9_]+", BRIDGE.read_text()))

    assert names == {"DATABRICKS_CONFIG_PROFILE", "DATABRICKS_CONFIG_FILE", "DATABRICKS_CLI"}


def test_genie_connection_lists_the_cli_profiles_when_its_own_is_not_set_up(start, genie, cli):
    cli.set_profiles({"dev": workspace("dev-workspace"), "DEFAULT": (WORKSPACE, "pat")})
    bridge = start(DATABRICKS_CONFIG_PROFILE="NEW")
    _, report = bridge.connection(3)

    assert report["status"] == "not_configured"
    assert report["profile"] == "NEW"
    assert report["profiles"] == [{"name": "dev", "host": workspace("dev-workspace"), "auth_type": "databricks-cli"},
                                  {"name": "DEFAULT", "host": WORKSPACE, "auth_type": "pat"}]
    step = report["next_step"]
    assert "workspace URL" in step and "never guess" in step and "genie_setup" in step and "browser" in step
    assert "DATABRICKS_CONFIG_PROFILE" in step
    assert "auth profiles --skip-validate --output json" in cli_calls(cli)
    assert genie.requests == []


def test_genie_connection_handles_a_machine_with_no_cli_profiles_at_all(start, cli):
    cli.set_profiles({})  # an empty config file: the real CLI prints {"profiles": null}
    bridge = start(DATABRICKS_CONFIG_PROFILE="NEW", GENIE_MCP_URL="")
    _, report = bridge.connection(3)

    assert report["status"] == "not_configured"
    assert report["profiles"] == []
    assert report["host"] is None


def test_genie_connection_reports_an_expired_sign_in_and_says_to_sign_in_again(start):
    bridge = start(FAKE_CLI_FAIL="1")
    _, report = bridge.connection(3)

    assert report["status"] == "login_needed"
    assert "genie_setup" in report["next_step"] and WORKSPACE in report["next_step"]


def test_genie_connection_says_to_offer_the_official_installer_when_the_cli_is_missing(start, genie, tmp_path):
    bridge = start(DATABRICKS_CLI=str(tmp_path / "no-such-databricks"))
    _, report = bridge.connection(3)
    bridge.send(call("genie_ask", {"question": "Revenue last quarter?"}, number=4))
    ask = bridge.reply()

    assert report["status"] == "cli_missing"
    step = report["next_step"]
    # The gist of the skill's install step, for an agent that skipped the skill: ask first, and say why the CLI.
    for words in ("official installer", "only after the user approves", "never install it silently", "OAuth U2M",
                  "brew install databricks/tap/databricks", "databricks/setup-cli", "genie_connection again"):
        assert words in step
    assert ask["result"]["isError"] is True and "official installer" in text_of(ask)
    assert genie.requests == []


def test_a_cli_that_is_not_installed_anywhere_is_reported_as_missing(monkeypatch, tmp_path, loaded):
    bridge = loaded
    monkeypatch.setattr(bridge, "cli_candidates", lambda env: [str(tmp_path / "databricks")])
    context = bridge.new_context({"DATABRICKS_CONFIG_PROFILE": "NEW"})

    assert bridge.connection(context)["status"] == "cli_missing"


def test_the_bridge_says_databricks_cli_profile_wherever_it_tells_the_user_about_one():
    # "CLI profile" alone could be read as a setting of Antigravity's own `agy` CLI. Docstrings aren't shown to anyone.
    tree = ast.parse(BRIDGE.read_text())
    docstrings = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Expr)}
    texts = [node.value for node in ast.walk(tree)
             if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings]

    assert [text for text in texts if re.search(r"(?<!Databricks )CLI profile", text)] == []


def test_its_own_tools_are_answered_by_the_bridge_and_never_sent_to_genie(start, genie):
    bridge = start()
    bridge.send(TOOLS_LIST)
    bridge.reply()
    bridge.send(call("genie_connection"))
    bridge.reply()
    bridge.send(call("genie_setup", {"workspace_url": WORKSPACE}, number=4))
    bridge.reply()

    assert genie.methods() == ["initialize", "notifications/initialized", "tools/list"]


# genie_setup: create the profile with a background `databricks auth login`, never overwriting one


def test_first_use_sets_up_the_profile_then_connects_and_tells_antigravity_the_tools_changed(start, genie, cli):
    genie.reply = tool_reply("status: in_progress")
    cli.set_profiles({})
    bridge = start(DATABRICKS_CONFIG_PROFILE="NEW")
    bridge.send(INITIALIZE)
    bridge.reply()
    bridge.send(INITIALIZED)
    bridge.send(TOOLS_LIST)
    bridge.reply()
    assert bridge.connection(3)[1]["status"] == "not_configured"

    bridge.send(call("genie_setup", {"workspace_url": WORKSPACE + "/browse?o=123"}, number=4))
    started = report_of(bridge.reply())
    notifications, report = wait_for_sign_in_to_end(bridge)
    bridge.send(call("genie_ask", {"question": "Top products by revenue?"}, number=5))
    ask = bridge.reply()

    assert started["status"] == "login_in_progress" and "browser" in started["next_step"]
    assert cli.logins() == [f"auth login --host {WORKSPACE} --profile NEW --timeout 5m"]
    assert f"[NEW]\nhost = {WORKSPACE}\n" in cli.config.read_text()
    assert notifications == [LIST_CHANGED]
    assert report["status"] == "ready" and report["profile"] == "NEW"
    assert genie.requests[-1]["body"]["params"]["name"] == "genie_ask"
    assert f"Asked Genie in {genie.host} (Databricks CLI profile NEW)" in text_of(ask)


def test_tells_antigravity_the_tools_changed_after_a_sign_in_even_if_it_never_listed_them(start, cli):
    cli.set_profiles({})
    bridge = start(DATABRICKS_CONFIG_PROFILE="NEW")
    bridge.send(call("genie_setup", {"workspace_url": WORKSPACE}))
    bridge.reply()
    notifications, report = wait_for_sign_in_to_end(bridge)

    assert report["status"] == "ready"
    assert notifications == [LIST_CHANGED]


def test_genie_setup_returns_at_once_while_the_user_signs_in(start, cli):
    cli.set_profiles({})
    bridge = start(DATABRICKS_CONFIG_PROFILE="NEW", FAKE_LOGIN_SECONDS="3")
    began = time.monotonic()
    bridge.send(call("genie_setup", {"workspace_url": WORKSPACE}))
    report = report_of(bridge.reply())

    assert time.monotonic() - began < 2
    assert report["status"] == "login_in_progress"
    assert bridge.connection(4)[1]["status"] == "login_in_progress"


def test_runs_only_one_sign_in_at_a_time(start, cli):
    cli.set_profiles({})
    bridge = start(DATABRICKS_CONFIG_PROFILE="NEW", FAKE_LOGIN_SECONDS="2")
    bridge.send(call("genie_setup", {"workspace_url": WORKSPACE}))
    bridge.reply()
    bridge.send(call("genie_setup", {"workspace_url": WORKSPACE}, number=4))
    report = report_of(bridge.reply())
    time.sleep(0.5)

    assert report["status"] == "login_in_progress"
    assert len(cli.logins()) == 1


def test_the_sign_in_keeps_going_after_antigravity_stops_the_bridge(start, cli):
    # A headless `agy -p` run ends after the agent's turn, which stops the bridge while the user is still signing in.
    cli.set_profiles({})
    bridge = start(new_session=True, DATABRICKS_CONFIG_PROFILE="NEW", FAKE_LOGIN_SECONDS="1")
    bridge.send(call("genie_setup", {"workspace_url": WORKSPACE}))
    bridge.reply()
    wait_for(lambda: cli.logins())
    os.killpg(bridge.process.pid, signal.SIGTERM)
    bridge.process.wait(timeout=10)

    wait_for(lambda: "[NEW]" in cli.config.read_text())


def test_a_failed_or_cancelled_sign_in_is_reported_with_how_to_retry(start, cli):
    cli.set_profiles({})
    bridge = start(DATABRICKS_CONFIG_PROFILE="NEW", FAKE_LOGIN_FAIL="1")
    bridge.send(call("genie_setup", {"workspace_url": WORKSPACE}))
    bridge.reply()
    _, report = wait_for_sign_in_to_end(bridge)

    assert report["status"] == "login_failed"
    assert "cancelled" in report["next_step"] and "genie_setup" in report["next_step"]


def test_genie_setup_signs_an_expired_profile_in_again_to_its_own_workspace(start, cli):
    bridge = start(FAKE_CLI_FAIL="1")
    bridge.send(call("genie_setup", {"workspace_url": WORKSPACE.upper() + "/sql/editor"}))
    report = report_of(bridge.reply())

    assert report["status"] == "login_in_progress"
    wait_for(lambda: cli.logins())
    assert cli.logins() == [f"auth login --host {WORKSPACE} --profile DEMO --timeout 5m"]


@pytest.mark.parametrize("expired", [False, True])
def test_genie_setup_never_changes_a_profile_that_is_set_up_for_another_workspace(start, cli, expired):
    before = cli.config.read_text()
    bridge = start(**({"FAKE_CLI_FAIL": "1"} if expired else {}))
    bridge.send(call("genie_setup", {"workspace_url": workspace("other-workspace")}))
    reply = bridge.reply()
    time.sleep(0.3)

    assert reply["result"]["isError"] is True
    assert "never changes" in text_of(reply) and "DATABRICKS_CONFIG_PROFILE" in text_of(reply)
    assert cli.logins() == []
    assert cli.config.read_text() == before


def test_genie_setup_leaves_a_signed_in_profile_alone(start, cli):
    bridge = start()
    bridge.send(call("genie_setup", {"workspace_url": WORKSPACE}))
    report = report_of(bridge.reply())
    time.sleep(0.3)

    assert report["status"] == "ready"
    assert cli.logins() == []


def test_genie_setup_refuses_a_url_that_is_not_a_databricks_workspace(start, cli):
    cli.set_profiles({})
    bridge = start(DATABRICKS_CONFIG_PROFILE="NEW")
    bridge.send(call("genie_setup", {"workspace_url": "http://intranet.example.test"}))
    reply = bridge.reply()
    time.sleep(0.3)

    assert reply["result"]["isError"] is True
    assert "https" in text_of(reply) and "never guess" in text_of(reply)
    assert cli.logins() == []


@pytest.mark.parametrize("profile", ["two words", "a]b", "-x", "x" * 65])
def test_genie_setup_refuses_a_profile_name_the_cli_config_cannot_safely_hold(start, cli, profile):
    cli.set_profiles({})
    bridge = start(DATABRICKS_CONFIG_PROFILE=profile)
    bridge.send(call("genie_setup", {"workspace_url": WORKSPACE}))
    reply = bridge.reply()
    time.sleep(0.3)

    assert reply["result"]["isError"] is True and "DATABRICKS_CONFIG_PROFILE" in text_of(reply)
    assert cli.logins() == []


DOMAINS = ["cloud.databricks.com", "gcp.databricks.com", "azuredatabricks.net", "cloud.databricks.us", "databricks.azure.us"]


@pytest.mark.parametrize("domain", DOMAINS)
@pytest.mark.parametrize("typed", ["https://{host}", "https://{host}/", "https://{host}/browse?o=123#x", "HTTPS://{HOST}",
                                   "{host}", "  https://{host}:443  "])
def test_accepts_databricks_workspace_urls_on_every_cloud_and_keeps_only_the_host(domain, typed):
    host = f"adb-1234.5.{domain}"
    bridge = load_bridge()

    assert bridge.workspace_url(typed.format(host=host, HOST=host.upper())) == f"https://{host}"


@pytest.mark.parametrize("typed", [
    "http://{host}",  # not https
    "ftp://{host}",
    "https://{host}:8443",  # not the https port
    "https://someone:secret@{host}",  # credentials in the URL
    "https://{host}.example.test",  # a Databricks name in front of another domain
    "https://example.test/{host}",  # a Databricks name in the path only
    "https://{domain}",  # the domain with no workspace in front
    "https://accounts.{domain}",  # the account consoles, not workspaces
    "https://accounts-dod.{gov}",
    "https://login.databricks.com",
    "https://{label}.databricks.com",  # not a workspace domain
    "https://{label}_x.{domain}",  # not a valid host name
    "https://-{label}.{domain}",
    "https://",
    "",
    "   ",
])
def test_refuses_urls_that_are_not_a_databricks_workspace(typed):
    bridge = load_bridge()
    text = typed.format(host="company-workspace.cloud.databricks.com", domain="cloud.databricks.com", label="workspace",
                        gov="cloud.databricks.us")

    with pytest.raises(bridge.BridgeError):
        bridge.workspace_url(text)


@pytest.mark.parametrize("value", [None, 42, ["https://x"]])
def test_refuses_a_workspace_url_that_is_not_text(value):
    bridge = load_bridge()

    with pytest.raises(bridge.BridgeError):
        bridge.workspace_url(value)


# Genie's own tools before the connection is ready, and the workspace line on every answer


def test_genie_calls_before_setup_say_to_ask_for_the_workspace_url_and_never_reach_genie(start, genie, cli):
    cli.set_profiles({})
    bridge = start(DATABRICKS_CONFIG_PROFILE="NEW")
    bridge.send(call("genie_ask", {"question": "Revenue last quarter?"}))
    reply = bridge.reply()

    assert reply["id"] == 3 and reply["result"]["isError"] is True
    message = text_of(reply)
    assert "workspace URL" in message and "never guess" in message and "genie_setup" in message
    assert genie.requests == []


def test_a_failed_cli_sign_in_becomes_an_error_that_says_how_to_fix_it(start, genie):
    bridge = start(FAKE_CLI_FAIL="1")
    bridge.send(call("genie_ask", {"question": "Revenue last quarter?"}))
    reply = bridge.reply()

    assert reply["id"] == 3 and reply["result"]["isError"] is True
    assert "sign in again" in text_of(reply) and "genie_setup" in text_of(reply)
    assert genie.requests == []


def test_adds_the_workspace_line_to_each_genie_ask_result_only(start, genie):
    genie.reply = tool_reply("status: in_progress")
    bridge = start()
    bridge.send(call("genie_ask", {"question": "Revenue last quarter?"}))
    ask = bridge.reply()
    bridge.send(call("genie_poll_response", POLL, number=4))
    poll = bridge.reply()

    assert ask["result"]["content"] == [{"type": "text", "text": "status: in_progress"},
                                        {"type": "text", "text": f"Asked Genie in {genie.host} (Databricks CLI profile DEMO)"}]
    assert poll["result"]["content"] == [{"type": "text", "text": "status: in_progress"}]


# Hardening: clear errors, a time limit on every call, and no token in any output


def test_a_403_from_genie_points_at_the_ip_access_list_or_the_vpn(start, genie):
    genie.reply = lambda request: (403, {"Content-Type": "text/plain"}, "Forbidden")
    bridge = start()
    bridge.send(call("genie_ask", {"question": "Revenue last quarter?"}))
    message = text_of(bridge.reply())

    assert "HTTP 403" in message and "IP access list" in message and "VPN" in message


def test_a_404_when_opening_the_session_says_genie_one_is_not_available_in_the_workspace(start, genie):
    genie.reply = lambda request: (404, {"Content-Type": "text/plain"}, "Not Found")
    bridge = start()
    bridge.send(call("genie_ask", {"question": "Revenue last quarter?"}))
    message = text_of(bridge.reply())

    assert "HTTP 404" in message and "isn't available" in message and genie.host in message


def test_an_unreachable_workspace_becomes_an_error_that_names_it(start):
    bridge = start(GENIE_MCP_URL="http://127.0.0.1:9/mcp")  # nothing listens on port 9
    bridge.send(call("genie_ask", {"question": "Revenue last quarter?"}))
    message = text_of(bridge.reply())

    assert "Couldn't reach Genie at 127.0.0.1:9" in message and "VPN" in message


def test_never_writes_the_token_to_its_output(start, genie, cli):
    genie.reply = lambda request: (500, {"Content-Type": "text/plain"}, "boom")
    cli.set_profiles({})
    bridge = start(DATABRICKS_CONFIG_PROFILE="NEW")
    bridge.send(INITIALIZE)
    bridge.reply()
    bridge.send(TOOLS_LIST)
    bridge.reply()
    bridge.send(call("genie_setup", {"workspace_url": WORKSPACE}))
    bridge.reply()
    wait_for_sign_in_to_end(bridge)
    for number, request in enumerate([TOOLS_LIST, call("genie_ask", {"question": "q"}), call("genie_connection")], 200):
        bridge.send({**request, "id": number})
        bridge.reply()
    stderr = bridge.close()

    assert genie.requests  # the token was used
    assert "tok-1" not in "".join(bridge.output) + stderr and "tok-2" not in "".join(bridge.output) + stderr


def load_bridge():
    """The bridge module loaded in this process, so a test can replace `urlopen`."""
    spec = importlib.util.spec_from_file_location("genie_bridge", BRIDGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def loaded(monkeypatch, tmp_path):
    """The bridge loaded in this process, with tmp_path as its home folder, so nothing is read from or written to the real one."""
    bridge = load_bridge()
    monkeypatch.setenv("HOME", str(tmp_path))
    return bridge


class FakeCli:
    login_hint = "Run: databricks auth login --profile TEST"
    profile = "TEST"

    def token(self):
        return "tok-test"

    def invalidate(self):
        pass


class FakeResponse:
    status = 200
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False

    def read(self):
        return b'{"jsonrpc":"2.0","id":3,"result":{}}'


class FakeClock:
    """Stands in for the bridge's `time` module, with a clock the test moves by hand."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def time(self):
        return self.now


def test_unexpected_errors_never_repeat_their_text_which_could_quote_a_token(monkeypatch, capsys, loaded):
    bridge = loaded

    def fail(message, context):
        raise ValueError("Invalid header value b'Bearer tok-secret'")

    monkeypatch.setattr(bridge, "call_upstream", fail)
    context = bridge.new_context({"GENIE_MCP_URL": LOOPBACK, "DATABRICKS_CONFIG_PROFILE": "TEST"})
    context["cli"] = FakeCli()

    bridge.handle(json.dumps(call("genie_poll_response", POLL)), context)
    output = capsys.readouterr()

    assert json.loads(output.out)["result"]["isError"] is True
    assert "tok-secret" not in output.out + output.err


def test_waits_up_to_150_seconds_for_genie():
    assert load_bridge().UPSTREAM_TIMEOUT_SECONDS == 150


def test_gives_up_before_antigravitys_three_minute_call_limit():
    """Antigravity cuts each MCP tool call off at 3 minutes (#28). The bridge handles one request at a time, so even a call
    that first needs a token from the CLI has to end before that, and leave the bridge free for the agent's next poll."""
    antigravity_call_limit_seconds = 180
    bridge = load_bridge()

    assert bridge.UPSTREAM_TIMEOUT_SECONDS + bridge.CLI_TIMEOUT_SECONDS <= antigravity_call_limit_seconds


def test_every_call_has_a_time_limit_well_under_antigravitys_three_minutes():
    bridge = load_bridge()

    assert bridge.UPSTREAM_TIMEOUT_SECONDS <= bridge.CALL_LIMIT_SECONDS <= 180 - 20


def slow_genie(monkeypatch, bridge, seconds_per_request):
    """Replace urlopen with a Genie that takes seconds_per_request on a fake clock; return the timeouts it was given."""
    clock = FakeClock()
    timeouts = []

    def urlopen(request, timeout=None):
        timeouts.append(timeout)
        clock.now += seconds_per_request
        return FakeResponse()

    monkeypatch.setattr(bridge, "time", clock)
    monkeypatch.setattr(bridge.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(bridge, "CALL_LIMIT_SECONDS", 160)
    monkeypatch.setattr(bridge, "UPSTREAM_TIMEOUT_SECONDS", 150)
    return timeouts


def test_each_step_of_a_call_gets_only_the_time_left_before_the_call_limit(monkeypatch, capsys, loaded):
    """A first Genie call also opens the session (initialize, then notifications/initialized), and all three requests
    have to fit in one call's time limit."""
    bridge = loaded
    timeouts = slow_genie(monkeypatch, bridge, 50)
    context = bridge.new_context({"GENIE_MCP_URL": LOOPBACK, "DATABRICKS_CONFIG_PROFILE": "TEST"})
    context["cli"] = FakeCli()

    bridge.handle(json.dumps(call("genie_poll_response", POLL)), context)

    assert timeouts == [150, 110, 60]
    assert json.loads(capsys.readouterr().out) == {"jsonrpc": "2.0", "id": 3, "result": {}}


def test_a_call_that_runs_out_of_time_says_so_instead_of_running_over(monkeypatch, capsys, loaded):
    bridge = loaded
    timeouts = slow_genie(monkeypatch, bridge, 80)
    context = bridge.new_context({"GENIE_MCP_URL": LOOPBACK, "DATABRICKS_CONFIG_PROFILE": "TEST"})
    context["cli"] = FakeCli()

    bridge.handle(json.dumps(call("genie_poll_response", POLL)), context)
    reply = json.loads(capsys.readouterr().out)

    assert timeouts == [150, 80]
    assert reply["result"]["isError"] is True and "ran out of time" in text_of(reply)


def test_gives_urlopen_the_upstream_timeout(monkeypatch):
    bridge = load_bridge()
    timeouts = []

    def fake_urlopen(request, timeout=None):
        timeouts.append(timeout)
        return FakeResponse()

    monkeypatch.setattr(bridge.urllib.request, "urlopen", fake_urlopen)
    context = {"url": "http://example.invalid/x", "cli": FakeCli(), "session_id": None}

    bridge.forward(INITIALIZE, context)

    assert timeouts == [bridge.UPSTREAM_TIMEOUT_SECONDS]


def test_gives_subprocess_run_the_cli_timeout(monkeypatch):
    bridge = load_bridge()
    timeouts = []

    def fake_run(command, **options):
        timeouts.append(options.get("timeout"))
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    monkeypatch.setattr(bridge, "CLI_TIMEOUT_SECONDS", 7)  # not the real value, so a hard-coded number would fail

    bridge.DatabricksCli({"DATABRICKS_CLI": "databricks"}).run(["auth", "token"], "Couldn't get a Databricks token from the CLI.")

    assert timeouts == [7]


# Review fixes: profiles that sign in another way, sign-ins that outlive the bridge, tool lists, versions, odd messages


@pytest.mark.parametrize("auth_type", ["pat", "oauth-m2m", "azure-cli", ""])
def test_a_profile_that_signs_in_another_way_is_reported_and_never_touched(start, genie, cli, auth_type):
    # `auth token` is U2M-only, and `auth login` would rewrite the profile and drop its token or client secret.
    cli.set_profiles({"DEMO": (WORKSPACE, auth_type)})
    before = cli.config.read_text()
    bridge = start()
    _, report = bridge.connection(3)
    bridge.send(call("genie_setup", {"workspace_url": WORKSPACE}, number=4))
    setup = bridge.reply()
    bridge.send(call("genie_ask", {"question": "Revenue last quarter?"}, number=5))
    ask = bridge.reply()
    time.sleep(0.3)

    assert report["status"] == "unsupported_auth"
    for words in ("OAuth U2M", "DATABRICKS_CONFIG_PROFILE", "won't change"):
        assert words in report["next_step"]
    assert setup["result"]["isError"] is True and "OAuth U2M" in text_of(setup)
    assert ask["result"]["isError"] is True and "unsupported_auth" in text_of(ask)
    assert cli.logins() == [] and cli.config.read_text() == before
    assert genie.requests == []


def test_a_restarted_bridge_still_sees_the_sign_in_in_progress_and_starts_no_second_one(start, cli):
    # `agy -p` starts a new bridge for each turn, while the user may still be signing in from the last one.
    cli.set_profiles({})
    first = start(new_session=True, DATABRICKS_CONFIG_PROFILE="NEW", FAKE_LOGIN_SECONDS="3")
    first.send(call("genie_setup", {"workspace_url": WORKSPACE}))
    first.reply()
    wait_for(lambda: cli.logins())
    os.killpg(first.process.pid, signal.SIGTERM)  # the turn ends, and the sign-in carries on
    first.process.wait(timeout=10)
    second = start(DATABRICKS_CONFIG_PROFILE="NEW")
    _, during = second.connection(3)
    second.send(call("genie_setup", {"workspace_url": WORKSPACE}, number=4))
    again = report_of(second.reply())
    _, after = wait_for_sign_in_to_end(second)

    assert during["status"] == "login_in_progress" and WORKSPACE in during["next_step"]
    assert again["status"] == "login_in_progress"
    assert len(cli.logins()) == 1
    assert after["status"] == "ready"


def test_a_restarted_bridge_reports_a_sign_in_that_ended_without_signing_in(start, cli):
    cli.set_profiles({})
    first = start(DATABRICKS_CONFIG_PROFILE="NEW", FAKE_LOGIN_FAIL="1")
    first.send(call("genie_setup", {"workspace_url": WORKSPACE}))
    first.reply()
    wait_for_sign_in_to_end(first)
    first.close()
    second = start(DATABRICKS_CONFIG_PROFILE="NEW")
    _, report = second.connection(3)

    assert report["status"] == "login_failed" and WORKSPACE in report["next_step"]


def test_the_sign_in_record_holds_no_token_and_is_removed_once_signed_in(start, cli):
    cli.set_profiles({})
    bridge = start(DATABRICKS_CONFIG_PROFILE="NEW", FAKE_LOGIN_SECONDS="1")
    bridge.send(call("genie_setup", {"workspace_url": WORKSPACE}))
    bridge.reply()
    (record_file,) = cli.login_records()
    record = json.loads(record_file.read_text())
    mode = record_file.stat().st_mode & 0o777
    _, report = wait_for_sign_in_to_end(bridge)

    assert set(record) == {"pid", "profile", "host", "started"}
    assert record["profile"] == "NEW" and record["host"] == WORKSPACE
    assert mode == 0o600
    assert report["status"] == "ready" and cli.login_records() == []


def test_an_old_sign_in_record_is_not_taken_for_one_still_running(start, cli):
    # Once a sign-in has ended, another process can get its process id.
    cli.set_profiles({})
    first = start(DATABRICKS_CONFIG_PROFILE="NEW", FAKE_LOGIN_FAIL="1")
    first.send(call("genie_setup", {"workspace_url": WORKSPACE}))
    first.reply()
    wait_for_sign_in_to_end(first)
    first.close()
    (record_file,) = cli.login_records()
    record = json.loads(record_file.read_text())
    record_file.write_text(json.dumps({**record, "pid": os.getpid(), "started": time.time() - 3600}))
    second = start(DATABRICKS_CONFIG_PROFILE="NEW")
    _, report = second.connection(3)

    assert report["status"] == "login_failed"


@pytest.mark.parametrize("answer", ["error", "nothing"])
def test_lists_the_bundled_tools_when_genie_answers_tools_list_with_an_error_or_nothing(start, genie, answer):
    def reply(request):
        body = request["body"]
        if body.get("method") != "tools/list":
            return 200, JSON, json.dumps({"jsonrpc": "2.0", "id": body.get("id"), "result": {}})
        if answer == "nothing":
            return 202, {}, ""
        return 200, JSON, json.dumps({"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32603, "message": "Internal error"}})

    genie.reply = reply
    bridge = start()
    bridge.send(TOOLS_LIST)

    assert [tool["name"] for tool in bridge.reply(timeout=5)["result"]["tools"]] == GENIE_TOOLS + BRIDGE_TOOLS


@pytest.mark.parametrize("asked, used", [("2025-03-26", "2025-03-26"), ("2099-01-01", "2025-06-18")])
def test_opens_the_genie_session_with_a_known_version_and_sends_it_on_later_requests(start, genie, asked, used):
    def reply(request):
        body = request["body"]
        result = {"protocolVersion": body["params"]["protocolVersion"]} if body.get("method") == "initialize" else {}
        return 200, JSON, json.dumps({"jsonrpc": "2.0", "id": body.get("id"), "result": result})

    genie.reply = reply
    bridge = start()
    bridge.send({**INITIALIZE, "params": {**INITIALIZE["params"], "protocolVersion": asked}})
    bridge.reply()
    bridge.send(TOOLS_LIST)
    bridge.reply()

    assert genie.requests[0]["body"]["params"]["protocolVersion"] == used
    assert "mcp-protocol-version" not in genie.requests[0]["headers"]
    assert [request["headers"].get("mcp-protocol-version") for request in genie.requests[1:]] == [used, used]


@pytest.mark.parametrize("message", [[INITIALIZE], "initialize", 42, None, {"jsonrpc": "2.0"}])
def test_answers_a_batch_or_anything_but_a_request_or_notification_with_invalid_request(start, genie, message):
    bridge = start()
    bridge.send(message)

    assert bridge.reply(timeout=5) == {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    assert genie.requests == []


# Review fixes: a wall-clock limit on the whole call, not just on each socket step


def short_limit(bridge, monkeypatch, env, seconds):
    """A context for the bridge loaded in this process, with a call limit of seconds and a CLI that answers at once."""
    monkeypatch.setattr(bridge, "CALL_LIMIT_SECONDS", seconds)
    context = bridge.new_context({"DATABRICKS_CONFIG_PROFILE": "TEST", **env})
    context["cli"] = FakeCli()
    return context


def test_a_slowly_streamed_reply_cannot_run_past_the_call_limit(monkeypatch, capsys, loaded, genie):
    # Each byte comes well within the socket timeout, so only a limit on the whole call stops this.
    genie.trickle = ("tools/call", 0.1)
    genie.reply = lambda request: (200, {"Content-Type": "text/event-stream"}, "data: " + json.dumps(
        {"jsonrpc": "2.0", "id": request["body"].get("id"), "result": {}}) + "\n\n")
    context = short_limit(loaded, monkeypatch, {"GENIE_MCP_URL": genie.url}, 2)
    began = time.monotonic()
    loaded.handle(json.dumps(call("genie_poll_response", POLL)), context)
    took = time.monotonic() - began
    reply = json.loads(capsys.readouterr().out)

    assert took < 3
    assert reply["result"]["isError"] is True and "ran out of time" in text_of(reply)


def test_a_request_stuck_inside_urlopen_cannot_run_past_the_call_limit(monkeypatch, capsys, loaded):
    # urlopen's timeout covers each socket step, not a DNS lookup or a connect that tries one address after another.
    def stuck(request, timeout=None):
        time.sleep(5)
        return FakeResponse()

    context = short_limit(loaded, monkeypatch, {"GENIE_MCP_URL": LOOPBACK}, 2)
    monkeypatch.setattr(loaded.urllib.request, "urlopen", stuck)
    began = time.monotonic()
    loaded.handle(json.dumps(call("genie_poll_response", POLL)), context)
    took = time.monotonic() - began

    assert took < 3
    assert "ran out of time" in text_of(json.loads(capsys.readouterr().out))


def test_a_token_refresh_after_a_401_gets_only_the_time_left(monkeypatch, capsys, loaded):
    bridge = loaded
    clock = FakeClock()
    cli_timeouts, url_timeouts = [], []
    tokens = iter(["tok-1", "tok-2"])

    def run(command, **options):
        cli_timeouts.append(options["timeout"])
        clock.now += 1
        reply = {"access_token": next(tokens), "token_type": "Bearer", "expires_in": 3600}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(reply), stderr="")

    def urlopen(request, timeout=None):
        url_timeouts.append(timeout)
        if request.get_header("Authorization") == "Bearer tok-1":
            clock.now += 140  # Genie takes its time, then rejects the token
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"expired"))
        clock.now += 1
        return FakeResponse()

    monkeypatch.setattr(bridge, "time", clock)
    monkeypatch.setattr(bridge.subprocess, "run", run)
    monkeypatch.setattr(bridge.urllib.request, "urlopen", urlopen)
    for name, value in (("CALL_LIMIT_SECONDS", 160), ("UPSTREAM_TIMEOUT_SECONDS", 150), ("CLI_TIMEOUT_SECONDS", 30)):
        monkeypatch.setattr(bridge, name, value)
    context = bridge.new_context({"GENIE_MCP_URL": LOOPBACK, "DATABRICKS_CLI": "databricks",
                                  "DATABRICKS_CONFIG_PROFILE": "TEST"})

    bridge.handle(json.dumps(call("genie_poll_response", POLL)), context)

    assert json.loads(capsys.readouterr().out) == {"jsonrpc": "2.0", "id": 3, "result": {}}
    assert cli_timeouts == [30, 19]  # the refresh after the 401 gets the 19 s left, not the CLI's usual 30
    assert url_timeouts == [150, 18, 17, 16]


# Second review (#33): a profile the CLI hides, pages that aren't JSON, where a sign-in is recorded, a question that timed out


SECRET = "service-principal-secret-xyz"


def azure_service_principal(profile):
    """A config file section that CLI v1.22.1's `auth profiles` hides, because it has neither a host nor an account_id:
    an Azure service principal that sets azure_workspace_resource_id."""
    return (f"[{profile}]\n"
            "azure_workspace_resource_id = /subscriptions/0000/resourceGroups/rg/providers/Microsoft.Databricks/workspaces/ws\n"
            f"azure_client_id = 1111\nazure_client_secret = {SECRET}\nazure_tenant_id = 2222\n")


def headerless_service_principal():
    """The same keys with no header at all, which the CLI's INI parser puts in DEFAULT."""
    return azure_service_principal("x").split("\n", 1)[1]


# What `databricks configure --host <host> --profile dev` wrote into a new config file (CLI v1.22.1), token made up.
CONFIGURE_LAYOUT = ("; The profile defined in the DEFAULT section is to be used as a fallback when no profile is explicitly "
                    f"specified.\n[DEFAULT]\n\n[dev]\nhost = {WORKSPACE}\ntoken = made-up-token\n\n[__settings__]\ndefault_profile = dev\n")


@pytest.mark.parametrize("profile", ["NEW", "DEFAULT"])  # [DEFAULT] is the section that configparser leaves out of its list
def test_genie_setup_never_signs_in_over_a_config_section_the_cli_does_not_list(start, cli, profile):
    # `auth login` would rewrite that section and drop its client secret.
    cli.set_config(azure_service_principal(profile))
    before = cli.config.read_text()
    bridge = start(DATABRICKS_CONFIG_PROFILE=profile)
    bridge.send(call("genie_setup", {"workspace_url": WORKSPACE}))
    reply = bridge.reply()
    time.sleep(0.3)

    assert cli.logins() == []
    assert cli.config.read_text() == before and SECRET in cli.config.read_text()
    assert reply["result"]["isError"] is True and "DATABRICKS_CONFIG_PROFILE" in text_of(reply)


def test_genie_connection_reports_a_profile_the_cli_does_not_list_but_the_config_file_holds(start, genie, cli):
    cli.set_profiles({"dev": workspace("dev-workspace")}, extra=azure_service_principal("NEW"))
    bridge = start(DATABRICKS_CONFIG_PROFILE="NEW")
    _, report = bridge.connection(3)
    bridge.send(call("genie_ask", {"question": "Revenue last quarter?"}, number=4))
    ask = bridge.reply()
    stderr = bridge.close()

    assert report["status"] == "profile_exists_unreadable" and report["profile"] == "NEW"
    for words in ("DATABRICKS_CONFIG_PROFILE", "new profile name", "won't touch", "genie_setup", "Never change this profile"):
        assert words in report["next_step"]
    assert ask["result"]["isError"] is True and "profile_exists_unreadable" in text_of(ask)
    assert genie.requests == []
    assert SECRET not in json.dumps(report) + "".join(bridge.output) + stderr  # only key names are read, never values


@pytest.mark.parametrize("variable, name", [("", ".databrickscfg"), ("~/custom.cfg", "custom.cfg")])
def test_looks_for_the_config_file_where_the_cli_does(start, cli, variable, name):
    # $DATABRICKS_CONFIG_FILE, with a leading ~ expanded to the home folder, else ~/.databrickscfg
    (cli.home / name).write_text(azure_service_principal("NEW"))
    bridge = start(DATABRICKS_CONFIG_PROFILE="NEW", DATABRICKS_CONFIG_FILE=variable)
    _, report = bridge.connection(3)

    assert report["status"] == "profile_exists_unreadable"


def test_a_config_file_the_cli_cannot_read_is_an_error_that_says_to_run_auth_profiles_and_starts_no_sign_in(start, cli, tmp_path):
    # The real CLI fails on a config file that is a folder, before the bridge gets as far as reading it itself.
    folder = tmp_path / "not-a-file"
    folder.mkdir()
    bridge = start(DATABRICKS_CONFIG_PROFILE="NEW", DATABRICKS_CONFIG_FILE=str(folder))
    bridge.send(call("genie_connection"))
    connection = bridge.reply()
    bridge.send(call("genie_setup", {"workspace_url": WORKSPACE}, number=4))
    setup = bridge.reply()
    time.sleep(0.3)

    assert connection["result"]["isError"] is True and "databricks auth profiles" in text_of(connection)
    assert setup["result"]["isError"] is True
    assert cli.logins() == []


def test_genie_setup_still_signs_in_again_a_profile_the_cli_lists_when_the_config_file_holds_it_too(start, cli):
    # The guard is for sections the CLI hides. A U2M profile it lists, whose sign-in has expired, is signed in again.
    cli.set_config(f"[DEMO]\nhost = {WORKSPACE}\nauth_type = databricks-cli\n")
    bridge = start(FAKE_CLI_FAIL="1")
    bridge.send(call("genie_setup", {"workspace_url": WORKSPACE}))
    report = report_of(bridge.reply())
    wait_for(lambda: cli.logins())

    assert report["status"] == "login_in_progress"
    assert cli.logins() == [f"auth login --host {WORKSPACE} --profile DEMO --timeout 5m"]


def test_reads_each_sections_key_names_and_never_a_value(tmp_path):
    path = tmp_path / "databrickscfg"
    path.write_bytes(
        b"\xef\xbb\xbf; a comment before any header, with [brackets] in it\r\norphan = before the first header\r\n"
        b"[DEFAULT]\r\nhost = https://x\r\n\r\n  [ spaced ]  \r\n"
        + f"token = {SECRET}\r\n[note] # a comment\r\n# [commented]\r\n; [also commented]\r\nkey: {SECRET}\r\n".encode()
        + b"[empty]\r\n[dev.eu]\r\nbad = \xff\xfe\r\n"
        + f"no delimiter {SECRET}\r\n[note]\r\nlater = 1\r\n".encode()
    )

    sections = load_bridge().config_sections(path)

    # Keys before the first header are DEFAULT's, a section named twice is one section, and a line with no delimiter is a
    # key with no name: its text could be part of a value.
    assert sections == {"DEFAULT": ["orphan", "host"], "spaced": ["token"], "note": ["key", "later"], "empty": [],
                        "dev.eu": ["bad", ""]}
    assert SECRET not in repr(sections)


def test_default_is_a_section_only_when_it_has_a_header_or_a_key(tmp_path):
    path = tmp_path / "databrickscfg"
    path.write_text(f"[dev]\nhost = {WORKSPACE}\n")
    assert load_bridge().config_sections(path) == {"dev": ["host"]}

    path.write_text(f"[DEFAULT]\n\n[dev]\nhost = {WORKSPACE}\n")
    assert load_bridge().config_sections(path) == {"DEFAULT": [], "dev": ["host"]}


@pytest.mark.parametrize("config, profile, holds", [
    pytest.param(None, "NEW", False, id="no config file"),
    pytest.param("", "NEW", False, id="an empty file"),
    pytest.param("[NEW]\n# a comment\n; another\n", "NEW", False, id="a header with no keys"),
    pytest.param("[NEW]\nkey = value\n", "NEW", True, id="a key under the header"),
    pytest.param("[OTHER]\nkey = value\n", "NEW", False, id="keys under another header"),
    pytest.param("[DEFAULT]\n\n[dev]\nhost = x\n", "DEFAULT", False, id="an empty DEFAULT header"),
    pytest.param("key = value\n", "DEFAULT", True, id="a key before the first header is DEFAULT's"),
    pytest.param("key = value\n", "NEW", False, id="and not another profile's"),
    pytest.param("a folder", "NEW", True, id="a file that cannot be read"),
])
def test_a_profile_holds_settings_when_the_config_file_has_a_key_under_its_name(tmp_path, config, profile, holds):
    path = tmp_path / "databrickscfg"
    if config == "a folder":
        path.mkdir()
    elif config is not None:
        path.write_text(config)
    cli = load_bridge().DatabricksCli({"DATABRICKS_CONFIG_PROFILE": profile, "DATABRICKS_CONFIG_FILE": str(path)})

    assert cli.has_config_keys() is holds


@pytest.mark.parametrize("config, profile, status", [
    pytest.param(CONFIGURE_LAYOUT, "DEFAULT", "not_configured", id="the empty DEFAULT header configure writes"),
    pytest.param("[DEFAULT]\n", "DEFAULT", "not_configured", id="an empty DEFAULT header"),
    pytest.param("[NEW]\n", "NEW", "not_configured", id="an empty named section"),
    pytest.param(headerless_service_principal(), "DEFAULT", "profile_exists_unreadable", id="service principal keys with no header"),
    pytest.param(azure_service_principal("NEW"), "NEW", "profile_exists_unreadable", id="a named service principal"),
])
def test_genie_connection_reads_what_the_cli_writes_and_hides(start, cli, any_cli, config, profile, status):
    # Run on the stand-in and on the real CLI. Only a hidden section with keys in it has anything for a sign-in to lose.
    cli.set_config(config)
    bridge = start(DATABRICKS_CLI=any_cli, DATABRICKS_CONFIG_PROFILE=profile)
    _, report = bridge.connection(3)

    assert report["status"] == status


@pytest.mark.parametrize("config, profile", [
    pytest.param(CONFIGURE_LAYOUT, "DEFAULT", id="the empty DEFAULT header configure writes"),
    pytest.param("[DEFAULT]\n# nothing in it\n", "DEFAULT", id="an empty DEFAULT header with a comment"),
    pytest.param("[NEW]\n", "NEW", id="an empty named section"),
])
def test_genie_setup_signs_a_profile_in_when_its_hidden_section_has_no_keys(start, cli, config, profile):
    # `databricks configure` leaves an empty [DEFAULT] header in a new config. There is nothing in it to lose.
    cli.set_config(config)
    bridge = start(DATABRICKS_CONFIG_PROFILE=profile)
    bridge.send(call("genie_setup", {"workspace_url": WORKSPACE}))
    reply = bridge.reply()

    assert "isError" not in reply["result"], text_of(reply)
    assert report_of(reply)["status"] == "login_in_progress"
    _, after = wait_for_sign_in_to_end(bridge)

    assert cli.logins() == [f"auth login --host {WORKSPACE} --profile {profile} --timeout 5m"]
    assert after["status"] == "ready"


@pytest.mark.parametrize("config", [
    pytest.param(headerless_service_principal(), id="keys only"),
    pytest.param(headerless_service_principal() + f"\n[dev]\nhost = {WORKSPACE}\nauth_type = databricks-cli\n", id="and another profile after them"),
    pytest.param(headerless_service_principal() + "\n[DEFAULT]\n", id="and an empty DEFAULT header after them"),
])
def test_genie_setup_never_signs_in_over_keys_that_come_before_the_first_header(start, cli, config):
    # The CLI's INI parser puts those keys in DEFAULT, and `auth login --profile DEFAULT` would drop the client secret.
    cli.set_config(config)
    before = cli.config.read_text()
    bridge = start(DATABRICKS_CONFIG_PROFILE="DEFAULT")
    bridge.send(call("genie_setup", {"workspace_url": WORKSPACE}))
    reply = bridge.reply()
    time.sleep(0.3)

    assert cli.logins() == []
    assert cli.config.read_text() == before and SECRET in cli.config.read_text()
    assert reply["result"]["isError"] is True and "won't touch" in text_of(reply)


PAGE = "<html><body>Sign in to continue</body></html>"  # what a proxy or a single sign-on page answers with, status 200


@pytest.mark.parametrize("method", ["initialize", "tools/list"])
@pytest.mark.parametrize("content_type, body", [("text/html", PAGE), ("text/event-stream", "data: " + PAGE + "\n\n")])
def test_still_lists_the_tools_when_genie_answers_with_something_that_is_not_json(start, genie, method, content_type, body):
    def reply(request):
        if request["body"].get("method") == method:
            return 200, {"Content-Type": content_type}, body
        return 200, JSON, json.dumps({"jsonrpc": "2.0", "id": request["body"].get("id"), "result": {}})

    genie.reply = reply
    bridge = start()
    bridge.send(TOOLS_LIST)
    answer = bridge.reply(timeout=5)

    assert "error" not in answer, answer
    assert [tool["name"] for tool in answer["result"]["tools"]] == GENIE_TOOLS + BRIDGE_TOOLS


def test_a_page_that_is_not_json_becomes_an_error_that_points_at_the_vpn_the_proxy_or_the_sign_in(start, genie):
    genie.reply = lambda request: (200, {"Content-Type": "text/html"}, PAGE)
    bridge = start()
    bridge.send(call("genie_ask", {"question": "Revenue last quarter?"}))
    reply = bridge.reply()

    assert reply["result"]["isError"] is True
    assert "Genie returned a non-JSON page; check VPN, proxy or sign-in" in text_of(reply)
    assert "Sign in to continue" not in text_of(reply)  # the page itself is never repeated


def test_a_reply_that_is_not_even_text_is_a_page_that_is_not_json_too(monkeypatch, loaded):
    class NotText(FakeResponse):
        def read(self):
            return b"\xff\xfe\x00 not text"

    monkeypatch.setattr(loaded.urllib.request, "urlopen", lambda request, timeout=None: NotText())
    context = {"url": "http://example.invalid/x", "cli": FakeCli(), "session_id": None}

    with pytest.raises(loaded.BridgeError, match="non-JSON page"):
        loaded.forward(INITIALIZE, context)


def test_lists_the_bundled_tools_whatever_goes_wrong_and_never_repeats_the_text_of_an_unexpected_error(monkeypatch, capsys, loaded):
    def fail(message, context):
        raise ValueError("Invalid header value b'Bearer tok-secret'")

    monkeypatch.setattr(loaded, "call_upstream", fail)
    context = loaded.new_context({"GENIE_MCP_URL": LOOPBACK, "DATABRICKS_CONFIG_PROFILE": "TEST"})
    context["cli"] = FakeCli()

    loaded.handle(json.dumps(TOOLS_LIST), context)
    output = capsys.readouterr()
    answer = json.loads(output.out)

    assert "error" not in answer, answer
    assert [tool["name"] for tool in answer["result"]["tools"]] == GENIE_TOOLS + BRIDGE_TOOLS
    assert "tok-secret" not in output.out + output.err


def mode_of(path):
    return stat.S_IMODE(path.stat().st_mode)


def start_sign_in(bridge, number=3):
    bridge.send(call("genie_setup", {"workspace_url": WORKSPACE}, number=number))
    return report_of(bridge.reply())


def test_the_sign_in_record_is_kept_in_a_private_folder_in_the_home_folder_not_in_the_shared_temp_folder(start, cli):
    cli.set_profiles({})
    bridge = start(DATABRICKS_CONFIG_PROFILE="NEW", FAKE_LOGIN_SECONDS="1")
    start_sign_in(bridge)

    assert list(cli.tmp.iterdir()) == []  # /tmp is shared, so anyone could plant or read a file there
    (record,) = cli.login_records()
    assert record.parent == cli.home / ".cache" / "antigravity-databricks-genie"
    assert mode_of(record.parent) == 0o700 and record.parent.stat().st_uid == os.getuid()
    assert mode_of(record) == 0o600
    wait_for_sign_in_to_end(bridge)


def test_a_record_folder_that_is_open_to_others_is_made_private(start, cli):
    cli.records.mkdir(parents=True)
    cli.records.chmod(0o755)
    cli.set_profiles({})
    bridge = start(DATABRICKS_CONFIG_PROFILE="NEW", FAKE_LOGIN_SECONDS="1")
    start_sign_in(bridge)

    assert mode_of(cli.records) == 0o700
    assert len(cli.login_records()) == 1
    wait_for_sign_in_to_end(bridge)


def test_a_record_folder_that_is_a_link_is_never_written_through(start, cli, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    cli.records.parent.mkdir(parents=True)
    cli.records.symlink_to(elsewhere, target_is_directory=True)
    cli.set_profiles({})
    bridge = start(DATABRICKS_CONFIG_PROFILE="NEW", FAKE_LOGIN_SECONDS="1")
    started = start_sign_in(bridge)
    _, report = wait_for_sign_in_to_end(bridge)

    assert started["status"] == "login_in_progress" and report["status"] == "ready"  # this bridge still tracks it
    assert list(elsewhere.iterdir()) == [] and list(cli.tmp.iterdir()) == []


def test_a_record_folder_owned_by_someone_else_is_not_used(monkeypatch, loaded):
    assert loaded.login_folder(create=True) is not None
    someone_else = os.getuid() + 1

    with monkeypatch.context() as patch:
        patch.setattr(loaded.os, "getuid", lambda: someone_else)

        assert loaded.login_folder() is None
        assert loaded.login_folder(create=True) is None


def test_checking_the_connection_creates_no_folder(start, cli):
    bridge = start()
    bridge.connection(3)

    assert not cli.records.parent.exists()


def never_answers_tool_calls(monkeypatch, bridge, how):
    """Replace urlopen with a Genie that answers everything but tools/call: that hangs, ends in a socket timeout while the
    reply is awaited (the request was sent), or ends in one while connecting (it was not)."""
    def urlopen(request, timeout=None):
        if json.loads(request.data)["method"] == "tools/call":
            if how == "socket":
                raise socket.timeout("timed out")
            if how == "connect":
                raise urllib.error.URLError(socket.timeout("timed out"))
            time.sleep(5)
        return FakeResponse()

    monkeypatch.setattr(bridge.urllib.request, "urlopen", urlopen)


def message_after_a_timeout(monkeypatch, capsys, loaded, how, name, arguments):
    context = short_limit(loaded, monkeypatch, {"GENIE_MCP_URL": LOOPBACK}, 2)
    never_answers_tool_calls(monkeypatch, loaded, how)
    loaded.handle(json.dumps(call(name, arguments)), context)
    reply = json.loads(capsys.readouterr().out)

    assert reply["result"]["isError"] is True
    return text_of(reply)


@pytest.mark.parametrize("how", ["hangs", "socket"])
def test_a_genie_ask_that_times_out_says_asking_again_starts_a_duplicate_question(monkeypatch, capsys, loaded, how):
    # Genie keeps working on a question after the bridge gives up, and the IDs it would have sent back are lost.
    message = message_after_a_timeout(monkeypatch, capsys, loaded, how, "genie_ask", {"question": "Revenue last quarter?"})

    assert "duplicate" in message and "wait" in message.lower()
    assert "same IDs" not in message and "genie_cancel_response" not in message  # no IDs, and no conversation to cancel


@pytest.mark.parametrize("how", ["hangs", "socket"])
def test_a_follow_up_that_times_out_suggests_cancelling_it_before_asking_again(monkeypatch, capsys, loaded, how):
    message = message_after_a_timeout(monkeypatch, capsys, loaded, how, "genie_ask",
                                      {"question": "By region?", "conversation_id": "c-1"})

    assert "duplicate" in message
    assert "genie_cancel_response" in message and "conversation_id c-1" in message
    assert message.index("genie_cancel_response") < message.lower().index("try again")  # cancel first, then ask


@pytest.mark.parametrize("how", ["hangs", "socket"])
def test_a_poll_that_times_out_gives_its_ids_back_for_polling(monkeypatch, capsys, loaded, how):
    message = message_after_a_timeout(monkeypatch, capsys, loaded, how, "genie_poll_response", POLL)

    assert "genie_poll_response" in message and "conversation_id c-1" in message and "response_id r-1" in message
    assert "duplicate" in message


@pytest.mark.parametrize("how", ["hangs", "socket"])
@pytest.mark.parametrize("name, arguments", [
    pytest.param("genie_get_query_result", {**POLL, "item_id": "i-1"}, id="fetching a result"),
    pytest.param("genie_cancel_response", {"conversation_id": "c-1"}, id="cancelling"),
])
def test_a_call_that_starts_no_question_just_says_to_try_again_when_it_times_out(monkeypatch, capsys, loaded, how, name, arguments):
    # Fetching a result or cancelling is no new question: nothing to poll for, and no duplicate to warn about.
    message = message_after_a_timeout(monkeypatch, capsys, loaded, how, name, arguments)

    assert "Try again" in message
    assert "duplicate" not in message and "genie_poll_response" not in message and "genie_cancel_response" not in message


def test_a_timeout_while_connecting_says_the_request_was_not_sent_and_warns_of_no_duplicate(monkeypatch, capsys, loaded):
    message = message_after_a_timeout(monkeypatch, capsys, loaded, "connect", "genie_ask", {"question": "Revenue last quarter?"})

    assert "wasn't sent" in message and "VPN" in message and "try again" in message.lower()
    assert "duplicate" not in message


def test_a_genie_ask_that_was_never_sent_for_lack_of_time_just_says_to_try_again(monkeypatch, capsys, loaded):
    timeouts = slow_genie(monkeypatch, loaded, 80)
    context = loaded.new_context({"GENIE_MCP_URL": LOOPBACK, "DATABRICKS_CONFIG_PROFILE": "TEST"})
    context["cli"] = FakeCli()

    loaded.handle(json.dumps(call("genie_ask", {"question": "Revenue last quarter?"})), context)
    message = text_of(json.loads(capsys.readouterr().out))

    assert timeouts == [150, 80]  # opening the session used up the time, so the question itself was never sent
    assert "Try again" in message and "duplicate" not in message


# Third review (#33): the stand-in CLI has to list profiles the way CLI v1.22.1 does. Every test below runs against the
# stand-in and, when the Databricks CLI is installed, against the real one, in a temporary folder used as HOME with
# DATABRICKS_CONFIG_FILE in it, so neither can read the real ~/.databrickscfg.


REAL_CLI = shutil.which("databricks")
needs_the_real_cli = pytest.mark.skipif(REAL_CLI is None, reason="the Databricks CLI isn't installed")


@pytest.fixture(params=["stand-in", pytest.param("real", marks=needs_the_real_cli)])
def any_cli(request, cli):
    return str(cli.path) if request.param == "stand-in" else REAL_CLI


def run_cli(binary, cli, *args, **overrides):
    """Run a databricks CLI on the config file in cli's temporary folder, with that folder as HOME and no DATABRICKS_*
    variable but the config file, so it never touches the real configuration. overrides change the environment."""
    env = {**without_databricks_variables(os.environ), "HOME": str(cli.home), "DATABRICKS_CONFIG_FILE": str(cli.config),
           "FAKE_CLI_CALLS": str(cli.log), **overrides}
    return subprocess.run([binary, *args], env=env, cwd=cli.home, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)


def listed_profiles(binary, cli, config):
    cli.set_config(config)
    result = run_cli(binary, cli, "auth", "profiles", "--skip-validate", "--output", "json")
    assert result.returncode == 0, result.stderr
    return [(profile["name"], profile.get("host")) for profile in json.loads(result.stdout)["profiles"] or []]


@pytest.mark.parametrize("config, expected", [
    pytest.param(CONFIGURE_LAYOUT, [("dev", WORKSPACE)], id="configure writes an empty DEFAULT header"),
    pytest.param(f"host = {WORKSPACE}\nauth_type = databricks-cli\n", [("DEFAULT", WORKSPACE)], id="keys before the first header are DEFAULT's"),
    pytest.param(f"host = {WORKSPACE}\n[DEFAULT]\nauth_type = databricks-cli\n", [("DEFAULT", WORKSPACE)], id="and join the DEFAULT header's"),
    pytest.param(headerless_service_principal(), [], id="service principal keys with no header"),
    pytest.param(azure_service_principal("sp"), [], id="service principal with no host"),
    pytest.param("[acct]\naccount_id = abc-123\n", [("acct", None)], id="an account_id alone is enough"),
    pytest.param("[empty]\n\n[DEFAULT]\n# only a comment\n; and another\n", [], id="sections with no keys"),
    pytest.param(f"[note] # a comment\nhost = {WORKSPACE}\n", [("note", WORKSPACE)], id="a comment after a header"),
    pytest.param(f"[colon]\nhost: {WORKSPACE}\n", [("colon", WORKSPACE)], id="a colon as the delimiter"),
    pytest.param(f"[crlf]\r\nhost = {WORKSPACE}\r\n", [("crlf", WORKSPACE)], id="CRLF line endings"),
])
def test_auth_profiles_lists_only_sections_with_a_host_or_an_account_id(any_cli, cli, config, expected):
    assert listed_profiles(any_cli, cli, config) == expected


@pytest.mark.parametrize("state, works", [("missing", False), ("a folder", False), ("empty", True)])
def test_auth_profiles_fails_without_a_readable_config_file_and_lists_none_for_an_empty_one(any_cli, cli, state, works):
    # A machine that never ran `databricks configure` or `auth login` has no config file, and the CLI fails on that.
    cli.config.unlink(missing_ok=True)
    if state == "a folder":
        cli.config.mkdir()
    if state == "empty":
        cli.set_config("")
    result = run_cli(any_cli, cli, "auth", "profiles", "--skip-validate", "--output", "json")

    assert (result.returncode == 0) is works
    if works:
        assert json.loads(result.stdout) == {"profiles": None}


@pytest.mark.parametrize("variable, name", [("", ".databrickscfg"), ("~/custom.cfg", "custom.cfg")])
def test_the_config_file_is_the_variable_with_a_leading_tilde_expanded_else_the_one_in_the_home_folder(any_cli, cli, variable, name):
    (cli.home / name).write_text(f"[dev]\nhost = {WORKSPACE}\n")
    result = run_cli(any_cli, cli, "auth", "profiles", "--skip-validate", "--output", "json", DATABRICKS_CONFIG_FILE=variable)

    assert result.returncode == 0, result.stderr
    assert [profile["name"] for profile in json.loads(result.stdout)["profiles"]] == ["dev"]


@pytest.mark.parametrize("config, profile", [
    pytest.param("[NEW]\nazure_workspace_resource_id = /subscriptions/0/x\nazure_client_secret = made-up\n", "NEW", id="a section with no host"),
    pytest.param("[DEFAULT]\n", "DEFAULT", id="an empty DEFAULT header"),
    pytest.param(f"[dev]\nhost = {WORKSPACE}\nauth_type = databricks-cli\n", "NEW", id="another profile only"),
])
def test_auth_token_fails_for_a_profile_that_is_not_listed(any_cli, cli, config, profile):
    cli.set_config(config)
    result = run_cli(any_cli, cli, "auth", "token", "--profile", profile, "--output", "json")

    assert result.returncode != 0 and result.stdout == ""


def test_a_machine_with_no_config_file_at_all_is_not_configured_and_can_be_set_up(start, cli):
    # A new machine has no config file, and the CLI fails on that instead of listing no profiles. First use starts there.
    cli.config.unlink()
    bridge = start(DATABRICKS_CONFIG_PROFILE="NEW")
    bridge.send(call("genie_connection"))
    first = bridge.reply()

    assert "isError" not in first["result"], text_of(first)
    assert report_of(first)["status"] == "not_configured" and report_of(first)["profiles"] == []

    bridge.send(call("genie_setup", {"workspace_url": WORKSPACE}, number=4))
    started = report_of(bridge.reply())
    _, after = wait_for_sign_in_to_end(bridge)

    assert started["status"] == "login_in_progress"
    assert cli.logins() == [f"auth login --host {WORKSPACE} --profile NEW --timeout 5m"]
    assert after["status"] == "ready"


@needs_the_real_cli
def test_with_the_real_cli_a_machine_with_no_config_file_is_not_configured(start, cli):
    cli.config.unlink()
    bridge = start(DATABRICKS_CLI=REAL_CLI, DATABRICKS_CONFIG_PROFILE="NEW")
    bridge.send(call("genie_connection"))
    reply = bridge.reply(timeout=60)

    assert "isError" not in reply["result"], text_of(reply)
    assert report_of(reply)["status"] == "not_configured"


def test_the_profiles_the_cli_lists_are_found_even_where_the_bridge_guesses_the_config_file_wrong(start, cli, tmp_path):
    # On Windows the CLI keeps its config under USERPROFILE, wherever HOME points, so the bridge's own guess can be a path that
    # doesn't exist while the CLI lists a profile. Taking that for "no profiles" would sign a PAT profile in over.
    cli.set_profiles({"DEMO": (WORKSPACE, "pat")})
    bridge = start(DATABRICKS_CONFIG_FILE=str(tmp_path / "no-such-file"), FAKE_CLI_CONFIG=str(cli.config))
    _, report = bridge.connection(3)
    bridge.send(call("genie_setup", {"workspace_url": WORKSPACE}, number=4))
    setup = bridge.reply()
    time.sleep(0.3)

    assert cli.logins() == []
    assert report["status"] == "unsupported_auth"
    assert setup["result"]["isError"] is True and "OAuth U2M" in text_of(setup)


def test_a_missing_config_file_does_not_hide_a_cli_that_is_missing_too(tmp_path):
    bridge = load_bridge()
    cli = bridge.DatabricksCli({"DATABRICKS_CLI": str(tmp_path / "no-such-databricks"),
                                "DATABRICKS_CONFIG_FILE": str(tmp_path / "no-such-config")})

    with pytest.raises(bridge.CliMissing):
        cli.profiles()


def test_a_missing_config_file_does_not_hide_a_call_that_has_run_out_of_time(monkeypatch, tmp_path):
    bridge = load_bridge()
    monkeypatch.setattr(bridge, "DEADLINE", bridge.time.monotonic() - 5)
    cli = bridge.DatabricksCli({"DATABRICKS_CLI": "databricks", "DATABRICKS_CONFIG_FILE": str(tmp_path / "no-such-config")})

    with pytest.raises(bridge.OutOfTime):
        cli.profiles()


def test_the_config_file_is_under_userprofile_on_windows_where_the_cli_looks_and_under_home_elsewhere(monkeypatch):
    bridge = load_bridge()
    env = {"HOME": "/git-bash/home", "USERPROFILE": "/windows/profile"}
    with monkeypatch.context() as patch:  # nothing but strings in here: Path would refuse to be built as a Windows path
        patch.setattr(bridge.os, "name", "nt")
        default = bridge.config_file(env)
        tilde = bridge.config_file({**env, "DATABRICKS_CONFIG_FILE": "~/custom.cfg"})

    assert default == os.path.join("/windows/profile", ".databrickscfg")
    assert tilde == os.path.join("/windows/profile", "custom.cfg")
    assert bridge.config_file(env) == os.path.join("/git-bash/home", ".databrickscfg")
