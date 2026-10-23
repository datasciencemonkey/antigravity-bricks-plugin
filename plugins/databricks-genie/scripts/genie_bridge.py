#!/usr/bin/env python3
"""Local MCP server that connects Antigravity to the Genie One MCP server, signing requests with the Databricks CLI login.

Antigravity starts it over stdio. The bridge answers `initialize`, `tools/list` and its own two tools, `genie_connection`
and `genie_setup`, itself, so they work before a CLI profile exists. Genie's tools are forwarded to Genie One once the
profile is signed in. Tokens come from `databricks auth token` and never leave this process. Standard library only, so
it runs on the python3 every developer machine already has (3.9 or later).
"""
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

VERSION = "0.3.0"
GENIE_ONE_PATH = "/ai-gateway/mcp-services/system.ai.genie_one_mcp"
REFRESH_MARGIN_SECONDS = 60
UPSTREAM_TIMEOUT_SECONDS = 150  # must end before Antigravity's 3-minute limit on each tool call
CLI_TIMEOUT_SECONDS = 30
CALL_LIMIT_SECONDS = 160  # all the work for one message, so every call ends well before Antigravity's 3-minute limit
LOGIN_TIMEOUT_MINUTES = 5  # a sign-in left unfinished in the browser gives up after this
PROTOCOL_VERSION = "2025-06-18"  # only when Antigravity's initialize doesn't name one
U2M = "databricks-cli"  # the auth type `databricks auth login` gives a profile: OAuth U2M, the only one Genie here can use
# Databricks workspace domains on AWS, GCP and Azure, and in the AWS GovCloud and Azure Government clouds.
WORKSPACE_DOMAINS = ("cloud.databricks.com", "gcp.databricks.com", "azuredatabricks.net", "cloud.databricks.us",
                     "databricks.azure.us")
DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
PROFILE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}")
TOOLS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "genie_tools.json")
INSTRUCTIONS = (
    "Databricks Genie answers questions about the user's data in their Databricks workspace. Call genie_connection "
    "once per conversation before the first question, and follow its next_step. Before each genie_ask, tell the user "
    "\"Asking Genie in <host> (CLI profile <profile>)\". Use genie_ask to start a question and genie_poll_response to "
    "get the answer. Never pick a workspace URL or a CLI profile for the user."
)
CLI_SIGN_IN = (
    "Genie signs in through the Databricks CLI, the safest way to run OAuth U2M (user-to-machine) sign-in from their "
    "own machine: they sign in in their browser with their usual single sign-on and MFA and never create or paste a "
    "token, and the CLI hands out short-lived tokens from its own local cache."
)
INSTALLERS = (
    "on a Mac with Homebrew, `brew install databricks/tap/databricks`; on macOS or Linux without Homebrew, "
    "`curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh`; on Windows, "
    "`winget install Databricks.DatabricksCLI`"
)
BRIDGE_TOOLS = [
    {
        "name": "genie_connection",
        "description": (
            "Check the Databricks connection Genie uses. Call it once per conversation before the first genie_ask, "
            "and again to follow a sign-in that genie_setup started. Returns JSON: status (ready, not_configured, "
            "login_needed, login_in_progress, login_failed, unsupported_auth or cli_missing), the CLI profile, the "
            "workspace host, and next_step, which says what to do. When the profile isn't set up, it also lists the "
            "CLI's other profiles."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "genie_setup",
        "description": (
            "Set up the Databricks CLI profile Genie uses. Starts `databricks auth login` for the workspace, which "
            "opens a browser window for the user to sign in, and returns at once; then call genie_connection to "
            "follow the sign-in. Pass the workspace URL the user gave you, and never guess one. It never points an "
            "existing profile at another workspace: when a profile's sign-in has expired, pass its own workspace URL."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_url": {
                    "type": "string",
                    "description": "The user's Databricks workspace URL, such as https://<workspace>.cloud.databricks.com",
                },
            },
            "required": ["workspace_url"],
        },
    },
]


def genie_url(host):
    base = host if host.startswith(("http://", "https://")) else "https://" + host
    return base.rstrip("/") + GENIE_ONE_PATH


def base_url(host):
    """A workspace as the user knows it: scheme and host, with no path."""
    parts = urllib.parse.urlsplit(host if "://" in host else "https://" + host)
    return "{}://{}".format(parts.scheme, parts.netloc)


def display_host(url):
    """The host (and port, if any) of a URL, as shown to the user."""
    return urllib.parse.urlsplit(url).netloc if url else None


def workspace_url(text):
    """The https://<host> of a Databricks workspace URL the user gave, or a BridgeError saying what's accepted."""
    problem = BridgeError(
        "genie_setup needs the https URL of a Databricks workspace, such as https://<workspace>.cloud.databricks.com, "
        "https://adb-<id>.<n>.azuredatabricks.net or https://<id>.<n>.gcp.databricks.com. Ask the user for theirs, "
        "and never guess one."
    )
    if not isinstance(text, str):
        raise problem
    text = text.strip()
    try:
        parts = urllib.parse.urlsplit(text if "://" in text else "https://" + text)
        port = parts.port
    except ValueError:
        raise problem
    host = parts.hostname or ""
    labels = host.split(".")
    if (parts.scheme != "https" or "@" in parts.netloc or port not in (None, 443) or labels[0] == "accounts"
            or not any(host.endswith("." + domain) for domain in WORKSPACE_DOMAINS)
            or not all(DNS_LABEL.fullmatch(label) for label in labels)):
        raise problem
    return "https://" + host


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


class CliMissing(BridgeError):
    pass


class SessionExpired(BridgeError):
    pass


class OutOfTime(BridgeError):
    pass


DEADLINE = None  # the time.monotonic() by which the message being handled must be answered; handle() sets it
OUT_OF_TIME = ("This call ran out of time before Genie answered. Try again; to wait for an answer that's already "
               "started, poll again with the same IDs.")


def check_time():
    """Raise OutOfTime once less than a second of this call's time limit is left."""
    if DEADLINE is not None and DEADLINE - time.monotonic() < 1:
        raise OutOfTime(OUT_OF_TIME)


def time_left(cap):
    """How long the next step may take: cap, or less when this call's time limit is closer."""
    check_time()
    return cap if DEADLINE is None else min(cap, DEADLINE - time.monotonic())


def in_time(work, seconds):
    """Run work() on a worker thread and wait at most seconds for it. Socket timeouts only bound each step, so without
    this a slow DNS lookup, a connect that tries one address after another, or a reply that trickles in could outlast
    the call."""
    outcome = {}

    def run():
        try:
            outcome["value"] = work()
        except BaseException as error:  # handed back to the caller
            outcome["error"] = error

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise OutOfTime(OUT_OF_TIME)
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


class DatabricksCli:
    """Everything the bridge needs from Databricks comes from one CLI profile: the workspace and fresh tokens."""

    def __init__(self, env):
        self.env = env
        self.profile = env.get("DATABRICKS_CONFIG_PROFILE") or "DEFAULT"
        self.login_hint = "Run: databricks auth login --profile " + self.profile
        # Let the profile decide the workspace and credentials, not variables inherited from the agent.
        self.cli_env = {key: value for key, value in env.items() if key not in ("DATABRICKS_HOST", "DATABRICKS_TOKEN")}
        self.cached = None  # (token, time to refresh it)

    def path(self):
        cli = self.env.get("DATABRICKS_CLI") or next((path for path in cli_candidates(self.env) if os.path.exists(path)), None)
        if not cli:
            raise CliMissing("The Databricks CLI wasn't found. Call genie_connection for how to install it.")
        return cli

    def run(self, args, failure):
        cli = self.path()
        try:
            # stdin is Antigravity's message stream, so the CLI must never read from it.
            result = subprocess.run([cli, *args, "--output", "json"], env=self.cli_env, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, timeout=time_left(CLI_TIMEOUT_SECONDS))
        except OSError:
            raise CliMissing("The Databricks CLI at {} couldn't be started. Call genie_connection for how to install it.".format(cli))
        except subprocess.TimeoutExpired:
            check_time()
            raise BridgeError(failure)
        if result.returncode != 0:
            raise BridgeError(failure)
        try:
            return json.loads(result.stdout)
        except ValueError:
            raise BridgeError("The Databricks CLI returned an unexpected response. " + failure)

    def profiles(self):
        """The CLI's profiles, each {"name", "host", "auth_type"}, from its config file: a local read, no network call."""
        reply = self.run(["auth", "profiles", "--skip-validate"],
                         "Couldn't read the Databricks CLI's profiles. Run `databricks auth profiles` in a terminal to see why.")
        return [{"name": item.get("name"), "host": item.get("host"), "auth_type": item.get("auth_type") or ""}
                for item in (reply.get("profiles") or []) if isinstance(item, dict)]

    def own_profile(self):
        return next((item for item in self.profiles() if item["name"] == self.profile), None)

    def token(self):
        if self.cached is None or time.time() >= self.cached[1]:
            reply = self.run(["auth", "token", "--profile", self.profile],
                             "Couldn't get a Databricks token from the CLI. " + self.login_hint)
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

    def login(self, url):
        """Start `databricks auth login` in the background: signing in in the browser can take longer than a tool call.

        It runs in its own session with no tie to Antigravity's pipes, so it keeps going when Antigravity stops the
        bridge, as a headless `agy -p` run does after each turn, and the sign-in still lands in the CLI's config.
        """
        command = [self.path(), "auth", "login", "--host", url, "--profile", self.profile,
                   "--timeout", "{}m".format(LOGIN_TIMEOUT_MINUTES)]
        try:
            return subprocess.Popen(command, env=self.cli_env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            raise CliMissing("The Databricks CLI at {} couldn't be started. Call genie_connection for how to install it.".format(command[0]))


def new_context(env):
    """The bridge's state for one Antigravity session. The CLI's config is the only thing that outlives it."""
    host = env.get("DATABRICKS_HOST")
    return {
        "override": env.get("GENIE_MCP_URL") or (genie_url(host) if host else None),
        "url": None,  # the Genie One URL in use, once the profile is ready
        "cli": DatabricksCli(env),
        "session_id": None,
        "session_started": False,
        "client_params": None,  # Antigravity's initialize, reused to open the session with Genie One
        "login": None,  # the background `databricks auth login` this bridge started
        "login_record": None,  # its record, also kept in the temp folder
        "ready": False,
        "announce_tools": False,  # tell Antigravity its tool list changed once Genie One's own list can be fetched
    }


def login_file(profile):
    """Where a background sign-in for profile is recorded: the OS temp folder, one file per user and profile."""
    if not PROFILE_NAME.fullmatch(profile):
        return None
    owner = os.getuid() if hasattr(os, "getuid") else "user"
    return os.path.join(tempfile.gettempdir(), "databricks-genie-login-{}-{}.json".format(owner, profile))


def read_login_file(profile):
    path = login_file(profile)
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as file:
            if hasattr(os, "getuid") and os.fstat(file.fileno()).st_uid != os.getuid():
                return None  # another user's file
            record = json.load(file)
    except (OSError, ValueError):
        return None
    valid = (isinstance(record, dict) and record.get("profile") == profile and isinstance(record.get("host"), str)
             and isinstance(record.get("pid"), int) and isinstance(record.get("started"), (int, float)))
    return record if valid else None


def write_login_file(record):
    """Save the sign-in's process id, profile, workspace and start time. There's never a token in it."""
    path = login_file(record["profile"])
    if not path:
        return
    try:
        handle, temporary = tempfile.mkstemp(prefix=".databricks-genie-login-", dir=os.path.dirname(path))
    except OSError:
        return  # this bridge still has the record in memory
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(record, file)
        os.replace(temporary, path)
    except OSError:
        try:
            os.remove(temporary)
        except OSError:
            pass


def login_record(context):
    """The background sign-in for this profile that hasn't been cleared yet, if there is one."""
    return read_login_file(context["cli"].profile) or context["login_record"]


def process_running(pid):
    if pid <= 0:
        return False
    if os.name == "nt":
        return True  # os.kill would stop the process on Windows; the age limit in login_running covers it
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True  # it runs, as someone else
    except OSError:
        return False
    return True


def login_running(context, record):
    process = context["login"]
    if process is not None and process.pid == record["pid"]:
        return process.poll() is None
    # A process id can be reused once the sign-in ends, so a record older than the sign-in's own time limit has ended.
    return time.time() - record["started"] < LOGIN_TIMEOUT_MINUTES * 60 + 60 and process_running(record["pid"])


def clear_login(context):
    if context["login"] is not None:
        context["login"].poll()  # collect it if it has ended
    context.update(login=None, login_record=None)
    path = login_file(context["cli"].profile)
    if path:
        try:
            os.remove(path)
        except OSError:
            pass


def next_step(status, profile, host, url, profiles, auth_type=None):
    """What the agent should do next. host is what the bridge calls; url is the workspace to sign in to."""
    if status == "ready":
        return "Before each genie_ask, tell the user \"Asking Genie in {} (CLI profile {})\".".format(host, profile)
    if status == "cli_missing":
        return ("The Databricks CLI isn't installed. Offer to install it with Databricks' official installer, and run "
                "the installer only after the user approves; never install it silently. When you ask, explain that "
                "{} Official installers: {}. Then call genie_connection again.".format(CLI_SIGN_IN, INSTALLERS))
    if status == "not_configured":
        step = ("CLI profile {} isn't set up yet. Ask the user for their Databricks workspace URL, such as "
                "https://<workspace>.cloud.databricks.com, and never guess one. Explain that {} A browser window will "
                "open for them to sign in. Then call genie_setup with the URL they give.".format(profile, CLI_SIGN_IN))
        if profiles:
            step += (" The CLI has other profiles, listed in profiles. If the user would rather use one of them, they "
                     "can set DATABRICKS_CONFIG_PROFILE to its name and restart Antigravity. Only profiles with auth_type "
                     "databricks-cli (OAuth U2M) work here, and never pick one for them.")
        return step
    if status == "unsupported_auth":
        method = "auth type " + auth_type if auth_type else "no auth type in its config (often a personal access token)"
        return ("CLI profile {} signs in with {}, not OAuth U2M. Genie in Antigravity signs in with OAuth U2M through "
                "the Databricks CLI, so it can't use this profile, and genie_setup won't change it. The user can set up "
                "a separate U2M profile: set DATABRICKS_CONFIG_PROFILE to a new profile name, restart Antigravity, and "
                "give you the workspace URL for genie_setup. Never change this profile for them.".format(profile, method))
    if status == "login_needed":
        if not url:
            return ("CLI profile {0} has no workspace URL. Ask the user to fix it in a terminal: "
                    "databricks auth login --profile {0}".format(profile))
        return ("The Databricks sign-in for CLI profile {} ({}) has expired. Tell the user a browser window will open "
                "for them to sign in again, then call genie_setup with {}.".format(profile, url, url))
    if status == "login_in_progress":
        return ("A browser window is open for the user to sign in to {} (CLI profile {}). Ask them to finish signing "
                "in there, then call genie_connection again. A sign-in left unfinished gives up after {} minutes."
                .format(url, profile, LOGIN_TIMEOUT_MINUTES))
    return ("The sign-in to {} didn't finish: it was cancelled in the browser, timed out, or failed. Ask the user "
            "whether to try again, and if so call genie_setup with {} again.".format(url, url))


def report(context, status, host=None, profiles=None, auth_type=None):
    """genie_connection's answer. host is the profile's or the sign-in's workspace; a URL override wins for display."""
    cli = context["cli"]
    if status == "ready":
        shown = display_host(context["url"])
    else:
        shown = display_host(context["override"] or (genie_url(host) if host else None))
    result = {"status": status, "profile": cli.profile, "host": shown,
              "next_step": next_step(status, cli.profile, shown, base_url(host) if host else None, profiles, auth_type)}
    if profiles is not None:
        result["profiles"] = profiles
    return result


def connection(context):
    """Where the connection stands: checks the CLI profile, and any background sign-in, even one an earlier bridge began."""
    cli = context["cli"]
    login = login_record(context)
    try:
        cli.token()
    except OutOfTime:
        raise
    except CliMissing:
        context["ready"] = False
        return report(context, "cli_missing")
    except BridgeError:
        context["ready"] = False
        if login is not None and login_running(context, login):
            return report(context, "login_in_progress", login["host"])
        profiles = cli.profiles()
        own = next((item for item in profiles if item["name"] == cli.profile), None)
        if own is not None and own["auth_type"] != U2M:  # `auth token` is U2M-only, so it always fails for these
            return report(context, "unsupported_auth", own["host"], auth_type=own["auth_type"])
        if login is not None:
            return report(context, "login_failed", login["host"])
        if own is None:
            return report(context, "not_configured", profiles=profiles)
        return report(context, "login_needed", own["host"])
    if login is not None:  # the sign-in worked: start again from the profile as it left it
        clear_login(context)
        context.update(ready=False, announce_tools=True)
    if not context["ready"]:  # it just turned ready: a fresh URL and Genie session
        url = context["override"]
        if not url:
            own = cli.own_profile()
            if not own or not own["host"]:
                raise BridgeError("CLI profile {0} has no workspace URL. Ask the user to fix it in a terminal: "
                                  "databricks auth login --profile {0}".format(cli.profile))
            url = genie_url(own["host"])
        context.update(url=url, session_id=None, session_started=False, ready=True)
        if context["announce_tools"]:
            context["announce_tools"] = False
            write({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
    return report(context, "ready")


def setup(arguments, context):
    """genie_setup: create the CLI profile, or sign it in again, with `databricks auth login` in the background."""
    cli = context["cli"]
    url = workspace_url(arguments.get("workspace_url"))
    state = connection(context)
    if state["status"] in ("cli_missing", "login_in_progress"):
        return state
    if state["status"] == "unsupported_auth":
        raise BridgeError(state["next_step"])
    own = cli.own_profile()
    if own is not None:
        if not own["host"] or urllib.parse.urlsplit(base_url(own["host"])).hostname != urllib.parse.urlsplit(url).hostname:
            raise BridgeError(
                "CLI profile {} is already set up for {}, and genie_setup never changes which workspace a profile points "
                "to. To use {}, the user can set DATABRICKS_CONFIG_PROFILE to a new profile name and restart Antigravity."
                .format(cli.profile, base_url(own["host"]) if own["host"] else "no workspace", url))
        if state["status"] == "ready":
            return state
        if own["auth_type"] != U2M:  # signing in again would rewrite the profile and drop its token or client secret
            raise BridgeError(next_step("unsupported_auth", cli.profile, None, None, None, own["auth_type"]))
    elif state["status"] == "ready":
        return state
    if not PROFILE_NAME.fullmatch(cli.profile):
        raise BridgeError(
            "The CLI profile name {!r} can only use letters, digits, '.', '_' and '-', up to 64 of them. Ask the user to "
            "set DATABRICKS_CONFIG_PROFILE to a simpler name and restart Antigravity.".format(cli.profile))
    process = cli.login(url)
    record = {"pid": process.pid, "profile": cli.profile, "host": url, "started": time.time()}
    context.update(login=process, login_record=record)
    write_login_file(record)
    return report(context, "login_in_progress", url)


def genie_tools():
    """Genie One's own tools, bundled with the plugin, to list before Genie One can be reached."""
    try:
        with open(TOOLS_FILE, encoding="utf-8") as tools:
            return json.load(tools)
    except (OSError, ValueError):
        return []


def parse_event_stream(text):
    messages = []
    for event in text.replace("\r\n", "\n").split("\n\n"):
        data = "\n".join(line[5:].lstrip() for line in event.split("\n") if line.startswith("data:"))
        if data.strip():
            messages.append(json.loads(data))
    return messages


def exchange(request, timeout):
    """One HTTP round trip to Genie One: its status, headers and body."""
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.headers, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read().decode(errors="replace")


def forward(message, context):
    """POST one message to Genie One and return the JSON-RPC messages it answers with."""
    for attempt in (0, 1):
        headers = {
            "Authorization": "Bearer " + context["cli"].token(),
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        session = context["session_id"]
        if session:
            headers["Mcp-Session-Id"] = session
        request = urllib.request.Request(context["url"], data=json.dumps(message).encode(), headers=headers, method="POST")
        host = display_host(context["url"])
        timeout = time_left(UPSTREAM_TIMEOUT_SECONDS)
        late = ("Genie didn't answer within {} seconds. If you were waiting for an answer, poll again with the same IDs "
                "instead of asking again.".format(int(timeout)))
        unreachable = "Couldn't reach Genie at {}: {}. Check the network connection, and connect to the VPN if the workspace needs one."
        try:
            status, response_headers, text = in_time(lambda: exchange(request, timeout), timeout)
        except urllib.error.URLError as error:
            raise BridgeError(late if isinstance(error.reason, socket.timeout) else unreachable.format(host, error.reason))
        except socket.timeout:
            raise BridgeError(late)
        except (OSError, http.client.HTTPException) as error:
            raise BridgeError(unreachable.format(host, error))
        if response_headers.get("Mcp-Session-Id"):
            context["session_id"] = response_headers["Mcp-Session-Id"]
        if status == 401 and attempt == 0:
            context["cli"].invalidate()
            continue
        if status == 401:
            raise BridgeError("Genie rejected the Databricks sign-in (HTTP 401). {}".format(context["cli"].login_hint))
        if status == 403:
            raise BridgeError(
                "Genie refused the request (HTTP 403). The workspace at {} may only accept connections from certain "
                "networks (an IP access list): connect to the VPN if it needs one, and try again. If that isn't it, "
                "check that the user can use Genie in that workspace.".format(host))
        if status == 404 and session:
            raise SessionExpired("Genie ended the session (HTTP 404). Try again.")
        if status == 404:
            raise BridgeError(
                "Genie One isn't available in the workspace at {} (HTTP 404). A workspace admin may need to turn it on; "
                "it needs partner-powered AI features.".format(host))
        if status >= 400:
            raise BridgeError("Genie returned HTTP {}: {}".format(status, text[:200]))
        if not text.strip():
            return []
        if "text/event-stream" in (response_headers.get("Content-Type") or ""):
            return parse_event_stream(text)
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else [parsed]


def start_session(context):
    """Open the session with Genie One the first time a request needs Genie, reusing Antigravity's initialize."""
    if context.get("session_started"):
        return
    context["session_id"] = None
    params = context.get("client_params") or {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                              "clientInfo": {"name": "databricks-genie-bridge", "version": VERSION}}
    replies = forward({"jsonrpc": "2.0", "id": "databricks-genie-bridge", "method": "initialize", "params": params}, context)
    error = next((reply["error"] for reply in replies if isinstance(reply, dict) and reply.get("error")), None)
    if error:
        raise BridgeError("Genie couldn't start a session: {}".format(error.get("message") if isinstance(error, dict) else error))
    forward({"jsonrpc": "2.0", "method": "notifications/initialized"}, context)
    context["session_started"] = True


def call_upstream(message, context):
    """Send a request to Genie One in a session. If Genie ended the session, open a new one and send it once more."""
    for attempt in (0, 1):
        try:
            start_session(context)
            return forward(message, context)
        except SessionExpired:
            context["session_started"] = False
            if attempt:
                raise


def matching_result(replies, message):
    """The result in Genie's reply to message, if there is one."""
    for reply in replies:
        if isinstance(reply, dict) and reply.get("id") == message.get("id") and isinstance(reply.get("result"), dict):
            return reply["result"]
    return None


def not_ready(state):
    return BridgeError("Genie isn't connected yet ({}). {}".format(state["status"], state["next_step"]))


def tool_result(message, text, error=False):
    result = {"content": [{"type": "text", "text": text}]}
    if error:
        result["isError"] = True
    return {"jsonrpc": "2.0", "id": message.get("id"), "result": result}


def initialize(message, context):
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    context.update(client_params=params, session_id=None, session_started=False)
    return {"jsonrpc": "2.0", "id": message.get("id"), "result": {
        "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
        "capabilities": {"tools": {"listChanged": True}},
        "serverInfo": {"name": "databricks-genie-bridge", "version": VERSION},
        "instructions": INSTRUCTIONS,
    }}


def list_tools(message, context):
    """Genie One's tools plus the bridge's. Until Genie One answers, its bundled list stands in."""
    try:
        if connection(context)["status"] == "ready":
            replies = call_upstream(message, context)
            result = matching_result(replies, message)
            if result is not None and isinstance(result.get("tools"), list):
                result["tools"] = result["tools"] + BRIDGE_TOOLS
                return replies
            sys.stderr.write("Genie One answered tools/list with an error or nothing, so the bundled list stands in.\n")
    except BridgeError as error:  # the tools still show up, and calling one says what's wrong
        sys.stderr.write("Listing Genie One's tools failed, so the bundled list stands in: {}\n".format(error))
    context["announce_tools"] = True
    return [{"jsonrpc": "2.0", "id": message.get("id"), "result": {"tools": genie_tools() + BRIDGE_TOOLS}}]


def call_tool(message, context):
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    name = params.get("name")
    if name == "genie_connection":
        return [tool_result(message, json.dumps(connection(context), indent=2))]
    if name == "genie_setup":
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        return [tool_result(message, json.dumps(setup(arguments, context), indent=2))]
    state = connection(context)
    if state["status"] != "ready":
        raise not_ready(state)  # never sent to Genie
    replies = call_upstream(message, context)
    result = matching_result(replies, message)
    if name == "genie_ask" and result is not None and isinstance(result.get("content"), list):
        result["content"].append({"type": "text", "text": "Asked Genie in {} (CLI profile {})".format(state["host"], state["profile"])})
    return replies


def dispatch(message, context):
    """The replies to one message from Antigravity."""
    method = message.get("method") if isinstance(message, dict) else None
    if method == "initialize":
        return [initialize(message, context)]
    if method == "ping":
        return [{"jsonrpc": "2.0", "id": message.get("id"), "result": {}}]
    if method == "tools/list":
        return list_tools(message, context)
    if method == "tools/call":
        return call_tool(message, context)
    if not isinstance(message, dict) or method is None or "id" not in message:
        # Notifications get no reply. Genie gets them once its session is open, except notifications/initialized:
        # the bridge sends its own when it opens that session.
        if method != "notifications/initialized" and context["session_started"]:
            forward(message, context)
        return []
    state = connection(context)
    if state["status"] != "ready":
        raise not_ready(state)
    return call_upstream(message, context)


def write(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def handle(line, context):
    global DEADLINE
    try:
        message = json.loads(line)
    except ValueError:
        return write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
    DEADLINE = time.monotonic() + CALL_LIMIT_SECONDS
    try:
        for reply in dispatch(message, context):
            write(reply)
    except Exception as error:  # every failure becomes an error the agent can read, never a crash
        # Only the bridge's own messages are repeated: another error's text could quote a request header, token included.
        text = str(error) if isinstance(error, BridgeError) else "Genie bridge error ({}).".format(type(error).__name__)
        if isinstance(message, dict) and message.get("id") is not None:
            if message.get("method") == "tools/call":  # in the result, so the agent sees it and can act on it
                write(tool_result(message, text, error=True))
            else:
                write({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32000, "message": text}})
        else:
            sys.stderr.write(text + "\n")


def main():
    context = new_context(dict(os.environ))
    # Handle messages one at a time so replies and the session id stay in order.
    for line in sys.stdin:
        if line.strip():
            handle(line, context)


if __name__ == "__main__":
    main()
