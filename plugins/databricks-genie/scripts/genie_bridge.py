#!/usr/bin/env python3
"""Local MCP server that connects Antigravity to the Genie One MCP server, signing requests with the Databricks CLI login.

Antigravity starts it over stdio. The bridge answers `initialize`, `tools/list` and its own two tools, `genie_connection`
and `genie_setup`, itself, so they work before a CLI profile exists. Genie's tools are forwarded to Genie One once the
profile is signed in. Tokens come from `databricks auth token` and never leave this process. Standard library only, so
it runs on the python3 every developer machine already has (3.9 or later).
"""
import http.client
import ipaddress
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

VERSION = "0.3.3"
GENIE_ONE_PATH = "/ai-gateway/mcp-services/system.ai.genie_one_mcp"
REFRESH_MARGIN_SECONDS = 60
UPSTREAM_TIMEOUT_SECONDS = 150  # must end before Antigravity's 3-minute limit on each tool call
CLI_TIMEOUT_SECONDS = 30
CALL_LIMIT_SECONDS = 160  # all the work for one message, so every call ends well before Antigravity's 3-minute limit
LOGIN_TIMEOUT_MINUTES = 5  # a sign-in left unfinished in the browser gives up after this
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")  # the MCP versions the bridge speaks, newest first
PROTOCOL_VERSION = SUPPORTED_VERSIONS[0]
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
    "\"Asking Genie in <host> (Databricks CLI profile <profile>)\". Use genie_ask to start a question and "
    "genie_poll_response to get the answer. Never pick a workspace URL or a Databricks CLI profile for the user."
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
            "login_needed, login_in_progress, login_failed, unsupported_auth, profile_exists_unreadable or "
            "cli_missing), the Databricks CLI profile, the workspace host, and next_step, which says what to do. When "
            "the profile isn't set up, it also lists the Databricks CLI's other profiles."
        ),
        "inputSchema": {"type": "object", "properties": {"profile": {
            "type": "string",
            "description": ("Optional. A Databricks CLI profile other than the default to check, to ask Genie in its workspace for "
                            "part of a question. Leave it out for the default profile."),
        }}},
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
PROFILE_ARGUMENT = {  # the bridge adds it to each of Genie's tools, and takes it out of each call before Genie gets it
    "type": "string",
    "description": (
        "Optional. The Databricks CLI profile of another workspace to ask, for the part of a question the user asked about "
        "there. Leave it out for the default profile. Pass the same profile on every follow-up, poll, result and cancel of a "
        "conversation that started there."
    ),
}
CONVERSATION_ID = re.compile(r"\bconversation_id\W{0,3}[:=]\s*\W{0,2}([A-Za-z0-9][A-Za-z0-9_-]{7,})")


def with_profile(tools):
    """Genie's tools, each with the bridge's optional profile argument in its input schema."""
    for tool in tools:
        schema = tool.get("inputSchema") if isinstance(tool, dict) else None
        if isinstance(schema, dict) and isinstance(schema.setdefault("properties", {}), dict):
            schema["properties"]["profile"] = dict(PROFILE_ARGUMENT)
    return tools


def without_profile(message):
    """message as Genie One gets it: a tool call's arguments without the bridge's own profile argument."""
    params = message.get("params")
    arguments = params.get("arguments") if isinstance(params, dict) else None
    if message.get("method") != "tools/call" or not isinstance(arguments, dict) or "profile" not in arguments:
        return message
    return {**message, "params": {**params, "arguments": {key: value for key, value in arguments.items() if key != "profile"}}}


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
    if (parts.scheme != "https" or "@" in parts.netloc or port not in (None, 443)
            or labels[0] == "accounts" or labels[0].startswith("accounts-")
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


def config_file(env):
    """The file the Databricks CLI keeps its profiles in: $DATABRICKS_CONFIG_FILE, else ~/.databrickscfg. The CLI's home folder
    is USERPROFILE on Windows, whatever HOME says, and HOME elsewhere."""
    home = (env.get("USERPROFILE") if os.name == "nt" else env.get("HOME")) or os.path.expanduser("~")
    path = env.get("DATABRICKS_CONFIG_FILE") or os.path.join(home, ".databrickscfg")
    return os.path.join(home, path[1:].lstrip("/\\")) if path.startswith("~") else path  # the CLI expands a leading ~


KEY_NAME = re.compile(r"([^=:]*)[=:]")  # the CLI's INI parser ends a key at the first = or :


def config_sections(path):
    """The sections of an INI config file, as {name: key names}. Only the names are kept: no value, which may be a secret, is
    parsed or stored, and a line with no delimiter gets an empty name, because its text could be part of a value. Keys before
    the first header belong to DEFAULT, as in the CLI's INI parser. (configparser would parse every value, and it leaves
    [DEFAULT] out of its sections.)"""
    sections = {}
    current = "DEFAULT"
    with open(path, encoding="utf-8-sig", errors="replace") as file:
        for line in file:
            line = line.strip()
            if not line or line[0] in "#;":
                continue
            if line.startswith("[") and "]" in line:
                current = line[1:line.index("]")].strip()
                sections.setdefault(current, [])
            else:
                key = KEY_NAME.match(line)
                sections.setdefault(current, []).append(key.group(1).strip() if key else "")
    return sections


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


def in_time(work, seconds, late=OUT_OF_TIME):
    """Run work() on a worker thread and wait at most seconds for it, or raise OutOfTime with the text late. Socket timeouts
    only bound each step, so without this a slow DNS lookup, a connect that tries one address after another, or a reply
    that trickles in could outlast the call."""
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
        raise OutOfTime(late)
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


class DatabricksCli:
    """Everything the bridge needs from Databricks comes from one CLI profile: the workspace and fresh tokens."""

    def __init__(self, env, profile=None):
        self.env = env
        self.profile = profile or env.get("DATABRICKS_CONFIG_PROFILE") or "DEFAULT"
        self.login_hint = "Run: databricks auth login --profile " + self.profile
        self.cached = None  # (token, time to refresh it)

    def path(self):
        # GENIE_BRIDGE_DATABRICKS_CLI is the tests' way to name the CLI; the Databricks namespace stays the CLI's own.
        cli = self.env.get("GENIE_BRIDGE_DATABRICKS_CLI") or next(
            (path for path in cli_candidates(self.env) if os.path.exists(path)), None)
        if not cli:
            raise CliMissing("The Databricks CLI wasn't found. Call genie_connection for how to install it.")
        return cli

    def run(self, args, failure):
        cli = self.path()
        try:
            # stdin is Antigravity's message stream, so the CLI must never read from it. The CLI owns its config and its
            # environment variables, so it gets the environment unchanged, as a terminal would give it.
            result = subprocess.run([cli, *args, "--output", "json"], env=self.env, stdin=subprocess.DEVNULL,
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
        """The CLI's profiles, each {"name", "host", "auth_type"}, from its config file: a local read, no network call. A machine
        that never ran `databricks configure` or `auth login` has no config file, and the CLI fails on that instead of listing none,
        so a failure with no config file where the bridge looks means no profiles. The CLI is asked first, because it may keep its
        config somewhere the bridge doesn't look (on Windows, under USERPROFILE), and then its answer is the one to trust."""
        try:
            reply = self.run(["auth", "profiles", "--skip-validate"],
                             "Couldn't read the Databricks CLI's profiles. Run `databricks auth profiles` in a terminal to see why.")
        except (CliMissing, OutOfTime):
            raise
        except BridgeError:
            if self.config_file_is_missing():
                return []
            raise
        return [{"name": item.get("name"), "host": item.get("host"), "auth_type": item.get("auth_type") or ""}
                for item in (reply.get("profiles") or []) if isinstance(item, dict)]

    def config_file_is_missing(self):
        try:
            os.stat(config_file(self.env))
        except FileNotFoundError:
            return True
        except OSError:
            pass  # not missing, only unreadable: the CLI says what is wrong with it
        return False

    def own_profile(self):
        return next((item for item in self.profiles() if item["name"] == self.profile), None)

    def has_config_keys(self):
        """Whether the config file holds settings for the profile: a key in a section named like it, even a section that
        `auth profiles` hides because it has no host or account_id. Signing in over those would rewrite them and drop a secret.
        An empty header, like the [DEFAULT] one that `databricks configure` writes, holds nothing to lose. A file that can't be
        read counts as holding settings, so nothing is signed in over it."""
        try:
            return bool(config_sections(config_file(self.env)).get(self.profile))
        except FileNotFoundError:
            return False
        except OSError:
            return True

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
            return subprocess.Popen(command, env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            raise CliMissing("The Databricks CLI at {} couldn't be started. Call genie_connection for how to install it.".format(command[0]))


def loopback_url(text):
    """text, if it's an http or https URL for this machine, such as a test server, and None for any other URL. The bridge sends the
    profile's token to the URL it calls, so only the profile's workspace, or this machine, may be where it goes."""
    try:
        parts = urllib.parse.urlsplit(text)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    if host != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                return None
        except ValueError:
            return None
    return text


def new_context(env):
    """The bridge's state for one Antigravity session. The CLI's config is the only thing that outlives it."""
    override = loopback_url(env.get("GENIE_MCP_URL") or "")
    if env.get("GENIE_MCP_URL") and not override:
        sys.stderr.write("GENIE_MCP_URL is ignored: only a loopback URL, for a test server on this machine, is accepted. "
                         "Genie is called at the workspace of the Databricks CLI profile.\n")
    return {
        "override": override,  # a workspace named in the environment never decides: the profile's host does
        "url": None,  # the Genie One URL in use, once the profile is ready
        "cli": DatabricksCli(env),
        "session_id": None,
        "session_started": False,
        "client_params": None,  # Antigravity's initialize, reused to open the session with Genie One
        "protocol_version": PROTOCOL_VERSION,  # agreed with Antigravity
        "upstream_version": None,  # agreed with Genie One, and sent on each request after initialize
        "login": None,  # the background `databricks auth login` this bridge started
        "login_record": None,  # its record, also kept in a private folder in the home folder
        "ready": False,
        "announce_tools": False,  # tell Antigravity its tool list changed once Genie One's own list can be fetched
        "env": env,  # the environment each profile's Databricks CLI gets, unchanged
        "workspaces": {},  # the state of each other Databricks CLI profile a tool call named, by name
        "conversations": {},  # the Databricks CLI profile of each Genie conversation the bridge saw Genie answer in
    }


def named_context(context, profile):
    """The bridge's state for a Databricks CLI profile that a tool call named, next to the default's: its own CLI login, workspace
    and Genie session. Antigravity's initialize, the environment and a test server's URL are the same for every profile."""
    state = dict(context, cli=DatabricksCli(context["env"], profile), url=None, session_id=None, session_started=False,
                 upstream_version=None, login=None, login_record=None, ready=False, announce_tools=False, named=True)
    del state["workspaces"], state["conversations"]  # only the default's state keeps them
    return state


def workspace_for(context, profile):
    """The state for profile: the default's when profile is None or the default's own name, else that profile's own."""
    if profile is None or profile == context["cli"].profile:
        return context
    if profile not in context["workspaces"]:
        context["workspaces"][profile] = named_context(context, profile)
    return context["workspaces"][profile]


def chosen_profile(arguments):
    """The Databricks CLI profile a tool call names, or None for the default. The name goes to the CLI, so it has to be one its
    config can hold."""
    profile = arguments.get("profile")
    if profile is None or (isinstance(profile, str) and not profile.strip()):
        return None
    if isinstance(profile, str) and PROFILE_NAME.fullmatch(profile.strip()):
        return profile.strip()
    raise BridgeError(
        "profile has to be the name of a Databricks CLI profile, such as one genie_connection lists: letters, digits, '.', '_' "
        "and '-', up to 64 of them. Leave it out for the default profile, and never pick one for the user.")


def genie_workspace(context, arguments):
    """Where a call to one of Genie's tools goes: the profile it names, else the profile its conversation started in, once the
    bridge has seen Genie answer there, else the default. A conversation stays in its workspace, so naming another profile for
    it is refused."""
    profile = chosen_profile(arguments)
    conversation = arguments.get("conversation_id")
    started = context["conversations"].get(conversation) if isinstance(conversation, str) else None
    if profile is not None and started is not None and profile != started:
        raise BridgeError(
            "Genie conversation {0} started in Databricks CLI profile {1}, and a conversation stays in its workspace: pass "
            "profile {1} to go on with it. To ask in Databricks CLI profile {2}, start a new conversation there, without "
            "conversation_id.".format(conversation, started, profile))
    return workspace_for(context, profile or started)


def conversation_in(result):
    """The conversation_id that a Genie answer names, in its structuredContent or else in its text, or None."""
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and isinstance(structured.get("conversation_id"), str) and structured["conversation_id"]:
        return structured["conversation_id"]
    for item in result.get("content") if isinstance(result.get("content"), list) else []:
        found = CONVERSATION_ID.search(item["text"]) if isinstance(item, dict) and isinstance(item.get("text"), str) else None
        if found:
            return found.group(1)
    return None


def login_folder(create=False):
    """The folder for sign-in records, ~/.cache/antigravity-databricks-genie, which only this user can use. With create, it is
    made when it's missing. None when there is no such folder or it can't be trusted: a link (it could point anywhere) or
    another user's folder. One that is open to others is made private."""
    path = os.path.join(os.path.expanduser("~"), ".cache", "antigravity-databricks-genie")
    try:
        if create:
            os.makedirs(path, mode=0o700, exist_ok=True)
        info = os.lstat(path)
        if not stat.S_ISDIR(info.st_mode):
            return None
        if hasattr(os, "getuid"):
            if info.st_uid != os.getuid():
                return None
            if info.st_mode & 0o077:
                os.chmod(path, 0o700)
    except OSError:
        return None
    return path


def login_file(profile, create=False):
    """Where a background sign-in for profile is recorded: one file per profile, in the private folder."""
    folder = login_folder(create) if PROFILE_NAME.fullmatch(profile) else None
    return os.path.join(folder, "databricks-genie-login-{}.json".format(profile)) if folder else None


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
    path = login_file(record["profile"], create=True)
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
        return "Before each genie_ask, tell the user \"Asking Genie in {} (Databricks CLI profile {})\".".format(
            host, profile)
    if status == "cli_missing":
        return ("The Databricks CLI isn't installed. Offer to install it with Databricks' official installer, and run "
                "the installer only after the user approves; never install it silently. When you ask, explain that "
                "{} Official installers: {}. Then call genie_connection again.".format(CLI_SIGN_IN, INSTALLERS))
    if status == "not_configured":
        step = ("Databricks CLI profile {} isn't set up yet. Ask the user for their Databricks workspace URL, such as "
                "https://<workspace>.cloud.databricks.com, and never guess one. Explain that {} A browser window will "
                "open for them to sign in. Then call genie_setup with the URL they give.".format(profile, CLI_SIGN_IN))
        if profiles:
            step += (" The Databricks CLI has other profiles, listed in profiles. If the user would rather use one of them, they "
                     "can set DATABRICKS_CONFIG_PROFILE to its name and restart Antigravity. Only profiles with auth_type "
                     "databricks-cli (OAuth U2M) work here, and never pick one for them.")
        return step
    if status == "unsupported_auth":
        method = "auth type " + auth_type if auth_type else "no auth type in its config (often a personal access token)"
        return ("Databricks CLI profile {} signs in with {}, not OAuth U2M. Genie in Antigravity signs in with OAuth U2M through "
                "the Databricks CLI, so it can't use this profile, and genie_setup won't change it. The user can set up "
                "a separate U2M profile: set DATABRICKS_CONFIG_PROFILE to a new profile name, restart Antigravity, and "
                "give you the workspace URL for genie_setup. Never change this profile for them.".format(profile, method))
    if status == "profile_exists_unreadable":
        return ("Databricks CLI profile {0} has settings in the Databricks config file that `databricks auth profiles` doesn't "
                "list (usually because it has no host, as with an Azure service principal that sets azure_workspace_resource_id), "
                "or that file can't be read. Genie can't tell how the profile signs in, and signing it in again would rewrite it "
                "and drop its secret, so genie_setup won't touch it. The user can set up a separate profile: set "
                "DATABRICKS_CONFIG_PROFILE to a new profile name, restart Antigravity, and give you the workspace URL for "
                "genie_setup. Never change this profile for them.".format(profile))
    if status == "login_needed":
        if not url:
            return ("Databricks CLI profile {0} has no workspace URL. Ask the user to fix it in a terminal: "
                    "databricks auth login --profile {0}".format(profile))
        return ("The sign-in for Databricks CLI profile {} ({}) has expired. Tell the user a browser window will open "
                "for them to sign in again, then call genie_setup with {}.".format(profile, url, url))
    if status == "login_in_progress":
        return ("A browser window is open for the user to sign in to {} (Databricks CLI profile {}). Ask them to finish signing "
                "in there, then call genie_connection again. A sign-in left unfinished gives up after {} minutes."
                .format(url, profile, LOGIN_TIMEOUT_MINUTES))
    return ("The sign-in to {} didn't finish: it was cancelled in the browser, timed out, or failed. Ask the user "
            "whether to try again, and if so call genie_setup with {} again.".format(url, url))


def named_next_step(status, profile, host, url, profiles, auth_type=None):
    """next_step for a Databricks CLI profile that a tool call named. It's for that part of the question only, so nothing here
    changes the default profile."""
    if status == "ready":
        return ("Before each genie_ask, tell the user \"Asking Genie in {0} (Databricks CLI profile {1})\", and pass profile {1} on "
                "every Genie call for this part of the question, follow-ups and polls included.".format(host, profile))
    if status == "not_configured":
        return ("Databricks CLI profile {} isn't set up. If the user meant one of the Databricks CLI profiles listed in profiles, "
                "ask them which one, and never pick one for them.".format(profile))
    if status == "unsupported_auth":
        method = "auth type " + auth_type if auth_type else "no auth type in its config (often a personal access token)"
        return ("Databricks CLI profile {} signs in with {}, not OAuth U2M. Genie in Antigravity signs in with OAuth U2M through "
                "the Databricks CLI, so it can't use this profile, and genie_setup won't change it. Ask the user which other "
                "Databricks CLI profile to use, and never change this one for them.".format(profile, method))
    if status == "profile_exists_unreadable":
        return ("Databricks CLI profile {} has settings in the Databricks config file that `databricks auth profiles` doesn't list, "
                "or that file can't be read, so Genie can't tell how it signs in, and genie_setup won't touch it. Ask the user "
                "which other Databricks CLI profile to use, and never change this one for them.".format(profile))
    if status == "login_needed" and url:
        return ("The sign-in for Databricks CLI profile {0} ({1}) has expired. Ask the user to sign in again in a terminal with "
                "databricks auth login --profile {0}, then call genie_connection again with profile {0}.".format(profile, url))
    if status == "login_in_progress":
        return ("A browser window is open for the user to sign in to {0} (Databricks CLI profile {1}). Ask them to finish signing "
                "in there, then call genie_connection again with profile {1}. A sign-in left unfinished gives up after {2} "
                "minutes.".format(url, profile, LOGIN_TIMEOUT_MINUTES))
    if status == "login_failed":
        return ("The sign-in to {0} for Databricks CLI profile {1} didn't finish: it was cancelled in the browser, timed out, or "
                "failed. Ask the user whether to sign in again in a terminal with databricks auth login --profile {1}."
                .format(url, profile))
    return next_step(status, profile, host, url, profiles, auth_type)  # the CLI is missing, or the profile has no workspace URL


def report(context, status, host=None, profiles=None, auth_type=None):
    """genie_connection's answer. host is the profile's or the sign-in's workspace; a URL override wins for display."""
    cli = context["cli"]
    if status == "ready":
        shown = display_host(context["url"])
    else:
        shown = display_host(context["override"] or (genie_url(host) if host else None))
    step = named_next_step if context.get("named") else next_step
    result = {"status": status, "profile": cli.profile, "host": shown,
              "next_step": step(status, cli.profile, shown, base_url(host) if host else None, profiles, auth_type)}
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
        if own is None and cli.has_config_keys():  # `auth login` would rewrite keys the CLI doesn't list, and drop a secret
            return report(context, "profile_exists_unreadable")
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
                raise BridgeError("Databricks CLI profile {0} has no workspace URL. Ask the user to fix it in a terminal: "
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
    if state["status"] in ("unsupported_auth", "profile_exists_unreadable"):
        raise BridgeError(state["next_step"])
    own = cli.own_profile()
    if own is not None:
        if not own["host"] or urllib.parse.urlsplit(base_url(own["host"])).hostname != urllib.parse.urlsplit(url).hostname:
            raise BridgeError(
                "Databricks CLI profile {} is already set up for {}, and genie_setup never changes which workspace a "
                "profile points to. To use {}, the user can set DATABRICKS_CONFIG_PROFILE to a new profile name and "
                "restart Antigravity."
                .format(cli.profile, base_url(own["host"]) if own["host"] else "no workspace", url))
        if state["status"] == "ready":
            return state
        if own["auth_type"] != U2M:  # signing in again would rewrite the profile and drop its token or client secret
            raise BridgeError(next_step("unsupported_auth", cli.profile, None, None, None, own["auth_type"]))
    elif state["status"] == "ready":
        return state
    if not PROFILE_NAME.fullmatch(cli.profile):
        raise BridgeError(
            "The Databricks CLI profile name {!r} can only use letters, digits, '.', '_' and '-', up to 64 of them. Ask the user to "
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
            return response.status, response.headers, response.read().decode(errors="replace")
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read().decode(errors="replace")


def late_text(message, seconds, profile=None):
    """What to tell the agent when Genie didn't answer message in time. The request may have reached Genie, which keeps working
    on a question after the bridge gives up, so asking again starts a duplicate. A poll gets its IDs back to poll again with. A
    question that was just asked has none, because they come in the reply that never arrived; a follow-up has its conversation,
    which can be cancelled first. Fetching a result or cancelling starts nothing, so those just say to try again. profile is a
    Databricks CLI profile the call went to other than the default, which the next call has to name again."""
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
    conversation, response = (value if isinstance(value, str) and value else None
                              for value in (arguments.get("conversation_id"), arguments.get("response_id")))
    text = "Genie didn't answer within {} seconds, so this call ran out of time.".format(seconds)
    if params.get("name") == "genie_poll_response" and conversation and response:
        return text + (" Genie may still be working on it, and asking the question again would start a duplicate: call "
                       "genie_poll_response again with conversation_id {} and response_id {}{}.".format(
                           conversation, response, ", and profile " + profile if profile else ""))
    if params.get("name") == "genie_ask":
        text += (" Genie may still be working on the question, but no response_id came back to poll with, and asking again "
                 "starts a duplicate question.")
        if conversation:
            return text + (" To stop the follow-up first, call genie_cancel_response with conversation_id {}{}. Then wait a "
                           "minute or two, and try again.".format(conversation, " and profile " + profile if profile else ""))
        return text + " Wait a minute or two, then try again."
    return text + " Try again."


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
        if context.get("upstream_version"):
            headers["MCP-Protocol-Version"] = context["upstream_version"]
        request = urllib.request.Request(context["url"], data=json.dumps(without_profile(message)).encode(), headers=headers,
                                         method="POST")
        host = display_host(context["url"])
        timeout = time_left(UPSTREAM_TIMEOUT_SECONDS)
        late = late_text(message, int(timeout), context["cli"].profile if context.get("named") else None)
        unreachable = "Couldn't reach Genie at {}: {}. Check the network connection, and connect to the VPN if the workspace needs one."
        try:
            status, response_headers, text = in_time(lambda: exchange(request, timeout), timeout, late)
        except urllib.error.URLError as error:
            if isinstance(error.reason, socket.timeout):  # a timeout while connecting, so Genie got nothing to answer
                raise BridgeError("Genie at {} didn't answer within {} seconds, and the request wasn't sent. Check the network "
                                  "connection, and connect to the VPN if the workspace needs one; then try again.".format(host, int(timeout)))
            raise BridgeError(unreachable.format(host, error.reason))
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
        try:
            if "text/event-stream" in (response_headers.get("Content-Type") or ""):
                return parse_event_stream(text)
            parsed = json.loads(text)
        except ValueError:  # an HTML page from a proxy or a single sign-on service, never repeated: it could hold anything
            raise BridgeError("Genie returned a non-JSON page; check VPN, proxy or sign-in. The request went to {}.".format(host))
        return parsed if isinstance(parsed, list) else [parsed]


def start_session(context):
    """Open the session with Genie One the first time a request needs Genie, reusing Antigravity's initialize."""
    if context.get("session_started"):
        return
    context.update(session_id=None, upstream_version=None)
    version = context.get("protocol_version") or PROTOCOL_VERSION
    params = dict(context.get("client_params") or {"capabilities": {},
                                                   "clientInfo": {"name": "databricks-genie-bridge", "version": VERSION}})
    params["protocolVersion"] = version  # the version agreed with Antigravity, never one the bridge doesn't know
    initialize_request = {"jsonrpc": "2.0", "id": "databricks-genie-bridge", "method": "initialize", "params": params}
    replies = forward(initialize_request, context)
    error = next((reply["error"] for reply in replies if isinstance(reply, dict) and reply.get("error")), None)
    if error:
        raise BridgeError("Genie couldn't start a session: {}".format(error.get("message") if isinstance(error, dict) else error))
    agreed = (matching_result(replies, initialize_request) or {}).get("protocolVersion")
    context["upstream_version"] = agreed if isinstance(agreed, str) and agreed else version
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
    asked = params.get("protocolVersion")
    version = asked if asked in SUPPORTED_VERSIONS else PROTOCOL_VERSION  # otherwise the newest the bridge speaks
    context.update(client_params=params, protocol_version=version, session_id=None, session_started=False)
    return {"jsonrpc": "2.0", "id": message.get("id"), "result": {
        "protocolVersion": version,
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
                result["tools"] = with_profile(result["tools"]) + BRIDGE_TOOLS
                return replies
            sys.stderr.write("Genie One answered tools/list with an error or nothing, so the bundled list stands in.\n")
    except Exception as error:  # whatever it was, the tools still show up, and calling one says what's wrong
        sys.stderr.write("Listing Genie One's tools failed, so the bundled list stands in: {}\n".format(describe(error)))
    context["announce_tools"] = True
    return [{"jsonrpc": "2.0", "id": message.get("id"), "result": {"tools": with_profile(genie_tools()) + BRIDGE_TOOLS}}]


def call_tool(message, context):
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    name = params.get("name")
    arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
    if name == "genie_connection":
        return [tool_result(message, json.dumps(connection(workspace_for(context, chosen_profile(arguments))), indent=2))]
    if name == "genie_setup":
        return [tool_result(message, json.dumps(setup(arguments, context), indent=2))]
    workspace = genie_workspace(context, arguments)
    state = connection(workspace)
    if state["status"] != "ready":
        raise not_ready(state)  # never sent to Genie
    replies = call_upstream(message, workspace)
    result = matching_result(replies, message)
    if name in ("genie_ask", "genie_poll_response") and result is not None and not result.get("isError"):
        conversation = conversation_in(result)
        if conversation:  # Genie answered in this workspace, so its follow-ups and polls go here too
            context["conversations"][conversation] = workspace["cli"].profile
    if name == "genie_ask" and result is not None and isinstance(result.get("content"), list):
        result["content"].append({"type": "text", "text": "Asked Genie in {} (Databricks CLI profile {})".format(state["host"], state["profile"])})
    return replies


def dispatch(message, context):
    """The replies to one message from Antigravity."""
    if not isinstance(message, dict) or ("method" not in message and "id" not in message):
        return [{"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}]  # batches too
    method = message.get("method")
    if method == "initialize":
        return [initialize(message, context)]
    if method == "ping":
        return [{"jsonrpc": "2.0", "id": message.get("id"), "result": {}}]
    if method == "tools/list":
        return list_tools(message, context)
    if method == "tools/call":
        return call_tool(message, context)
    if method is None or "id" not in message:
        # Notifications get no reply. Genie gets them once its session is open, except notifications/initialized:
        # the bridge sends its own when it opens that session.
        if method != "notifications/initialized" and context["session_started"]:
            forward(message, context)
        return []
    state = connection(context)
    if state["status"] != "ready":
        raise not_ready(state)
    return call_upstream(message, context)


def describe(error):
    """What to say about an error. Only the bridge's own messages are repeated: another error's text could quote a request
    header, token included."""
    return str(error) if isinstance(error, BridgeError) else "Genie bridge error ({}).".format(type(error).__name__)


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
        text = describe(error)
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
