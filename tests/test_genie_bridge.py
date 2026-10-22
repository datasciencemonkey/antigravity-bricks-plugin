"""The Genie bridge: a local stdio MCP server that forwards to the Genie One MCP, signed with the Databricks CLI login.

Runs the real bridge under Python against a stand-in `databricks` CLI and a stand-in Genie MCP server.
"""
import importlib.util
import json
import os
import queue
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

BRIDGE = Path(__file__).resolve().parent.parent / "plugins" / "databricks-genie" / "scripts" / "genie_bridge.py"
# Run the bridge under the oldest Python a developer is likely to have: Apple's /usr/bin/python3 is 3.9.
PYTHON = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else "python3"
INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}}
TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


class FakeGenie:
    """Records each request; `reply(request) -> (status, headers, body)` decides the response."""

    def __init__(self):
        self.requests = []
        self.reply = lambda request: (200, {"Content-Type": "application/json"},
                                      json.dumps({"jsonrpc": "2.0", "id": request["body"].get("id"), "result": {"ok": True}}))
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
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/mcp"


@pytest.fixture
def genie():
    fake = FakeGenie()
    yield fake
    fake.server.shutdown()


@pytest.fixture
def cli(tmp_path):
    """A stand-in `databricks` CLI: hands out FAKE_TOKENS in order, records each call; FAKE_CLI_FAIL makes it fail."""
    calls = tmp_path / "cli-calls"
    script = tmp_path / "databricks"
    script.write_text(
        "#!/bin/sh\n"
        'echo "$@" >> "$FAKE_CLI_CALLS"\n'
        'if [ -n "$FAKE_CLI_FAIL" ]; then echo "Error: refresh token is invalid" >&2; exit 1; fi\n'
        'if [ "$2" = "describe" ]; then printf \'{"details":{"host":"%s","auth_type":"databricks-cli"}}\' "$FAKE_HOST"; exit 0; fi\n'
        'n=$(grep -c "auth token" "$FAKE_CLI_CALLS")\n'
        'token=$(echo "$FAKE_TOKENS" | cut -d, -f"$n")\n'
        'printf \'{"access_token":"%s","token_type":"Bearer","expires_in":%s}\' "$token" "${FAKE_EXPIRES_IN:-3600}"\n'
    )
    script.chmod(0o755)
    return script, calls


class Bridge:
    def __init__(self, genie, cli, **env):
        script, calls = cli
        self.process = subprocess.Popen(
            [PYTHON, str(BRIDGE)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**os.environ, "GENIE_MCP_URL": genie.url, "DATABRICKS_CLI": str(script),
                 "DATABRICKS_CONFIG_PROFILE": "DEMO", "DATABRICKS_HOST": "", "FAKE_CLI_CALLS": str(calls), "FAKE_TOKENS": "tok-1,tok-2", **env},
        )
        self.lines = queue.Queue()
        threading.Thread(target=lambda: [self.lines.put(line) for line in self.process.stdout], daemon=True).start()

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def reply(self, timeout=10):
        return json.loads(self.lines.get(timeout=timeout))

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=10)
        return self.process.stderr.read()


@pytest.fixture
def start(genie, cli):
    bridges = []

    def _start(**env):
        bridges.append(Bridge(genie, cli, **env))
        return bridges[-1]

    yield _start
    for bridge in bridges:
        if bridge.process.poll() is None:
            bridge.process.kill()


def cli_calls(cli):
    _, calls = cli
    return calls.read_text().splitlines() if calls.exists() else []


def test_forwards_a_request_signed_with_the_cli_token_and_returns_the_reply(start, genie, cli):
    bridge = start()
    bridge.send(INITIALIZE)

    assert bridge.reply() == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    assert genie.requests[0]["body"] == INITIALIZE
    assert genie.requests[0]["headers"]["authorization"] == "Bearer tok-1"
    assert cli_calls(cli) == ["auth token --profile DEMO --output json"]


def test_sends_the_session_id_from_initialize_on_later_requests(start, genie):
    genie.reply = lambda request: (200, {"Content-Type": "application/json", "Mcp-Session-Id": "session-7"},
                                   json.dumps({"jsonrpc": "2.0", "id": request["body"].get("id"), "result": {}}))
    bridge = start()
    bridge.send(INITIALIZE)
    bridge.reply()
    bridge.send(TOOLS_LIST)
    bridge.reply()

    assert genie.requests[1]["headers"]["mcp-session-id"] == "session-7"


def test_forwards_notifications_without_writing_a_reply(start, genie):
    genie.reply = lambda request: ((202, {}, "") if "id" not in request["body"] else
                                   (200, {"Content-Type": "application/json"},
                                    json.dumps({"jsonrpc": "2.0", "id": request["body"]["id"], "result": {}})))
    bridge = start()
    bridge.send(INITIALIZED)
    bridge.send(TOOLS_LIST)

    assert bridge.reply()["id"] == 2
    assert genie.requests[0]["body"] == INITIALIZED


def test_unwraps_event_stream_replies(start, genie):
    message = {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}
    genie.reply = lambda request: (200, {"Content-Type": "text/event-stream"}, f"event: message\ndata: {json.dumps(message)}\n\n")
    bridge = start()
    bridge.send(TOOLS_LIST)

    assert bridge.reply() == message


def test_reuses_the_token_until_it_is_about_to_expire(start, cli):
    bridge = start()
    bridge.send(INITIALIZE)
    bridge.reply()
    bridge.send(TOOLS_LIST)
    bridge.reply()

    assert len(cli_calls(cli)) == 1


def test_gets_a_new_token_and_retries_once_when_the_token_is_rejected(start, genie, cli):
    def reply(request):
        if request["headers"]["authorization"] == "Bearer tok-1":
            return 401, {"Content-Type": "application/json"}, '{"error":"invalid token"}'
        return 200, {"Content-Type": "application/json"}, json.dumps({"jsonrpc": "2.0", "id": 2, "result": {}})

    genie.reply = reply
    bridge = start()
    bridge.send(TOOLS_LIST)

    assert bridge.reply() == {"jsonrpc": "2.0", "id": 2, "result": {}}
    assert [r["headers"]["authorization"] for r in genie.requests] == ["Bearer tok-1", "Bearer tok-2"]
    assert len(cli_calls(cli)) == 2


def test_a_failed_cli_sign_in_becomes_an_error_that_says_how_to_fix_it(start):
    bridge = start(FAKE_CLI_FAIL="1")
    bridge.send(TOOLS_LIST)
    reply = bridge.reply()

    assert reply["id"] == 2
    assert "databricks auth login --profile DEMO" in reply["error"]["message"]


def test_upstream_http_errors_become_json_rpc_errors(start, genie):
    genie.reply = lambda request: (500, {"Content-Type": "text/plain"}, "boom")
    bridge = start()
    bridge.send(TOOLS_LIST)
    reply = bridge.reply()

    assert reply["id"] == 2
    assert "HTTP 500" in reply["error"]["message"]


def test_never_writes_the_token_to_its_output(start, genie):
    genie.reply = lambda request: (500, {"Content-Type": "text/plain"}, "boom")
    bridge = start()
    bridge.send(TOOLS_LIST)
    reply = bridge.reply()
    stderr = bridge.close()

    assert "tok-1" not in json.dumps(reply) + stderr


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
    bridge = start(GENIE_MCP_URL="", FAKE_HOST=genie.url.rsplit("/", 1)[0])
    bridge.send(TOOLS_LIST)
    bridge.reply()

    assert genie.requests[0]["path"] == "/ai-gateway/mcp-services/system.ai.genie_one_mcp"
    assert "auth describe --profile DEMO --output json" in cli_calls(cli)


def test_uses_the_default_cli_profile_unless_one_is_set(start, cli):
    bridge = start(DATABRICKS_CONFIG_PROFILE="")
    bridge.send(TOOLS_LIST)
    bridge.reply()

    assert cli_calls(cli) == ["auth token --profile DEFAULT --output json"]


def test_a_failed_workspace_lookup_becomes_an_error_that_says_how_to_fix_it(start):
    bridge = start(GENIE_MCP_URL="", FAKE_CLI_FAIL="1")
    bridge.send(TOOLS_LIST)
    reply = bridge.reply()

    assert reply["id"] == 2
    assert "databricks auth login --profile DEMO" in reply["error"]["message"]


def test_asks_the_cli_for_a_new_token_shortly_before_the_old_one_expires(start, cli):
    # The CLI's tokens last an hour and the bridge renews a minute early, so a 61-second token is stale after a second.
    bridge = start(FAKE_EXPIRES_IN="61")
    bridge.send(TOOLS_LIST)
    bridge.reply()
    time.sleep(1.5)
    bridge.send({**TOOLS_LIST, "id": 3})
    bridge.reply()

    assert cli_calls(cli) == ["auth token --profile DEMO --output json"] * 2


def load_bridge():
    """The bridge module loaded in this process, so a test can replace `urlopen`."""
    spec = importlib.util.spec_from_file_location("genie_bridge", BRIDGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeCli:
    login_hint = "Run: databricks auth login --profile TEST"

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
        return b'{"jsonrpc":"2.0","id":1,"result":{}}'


def test_waits_up_to_150_seconds_for_genie():
    assert load_bridge().UPSTREAM_TIMEOUT_SECONDS == 150


def test_gives_up_before_antigravitys_three_minute_call_limit():
    """Antigravity cuts each MCP tool call off at 3 minutes (#28). The bridge handles one request at a time, so even a call
    that first needs a token from the CLI has to end before that, and leave the bridge free for the agent's next poll."""
    antigravity_call_limit_seconds = 180
    bridge = load_bridge()

    assert bridge.UPSTREAM_TIMEOUT_SECONDS + bridge.CLI_TIMEOUT_SECONDS <= antigravity_call_limit_seconds


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
