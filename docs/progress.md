# Progress: the Databricks Genie plugin for Google Antigravity

As of 2026-10-23. Finished items are crossed out; issue numbers refer to the [project board](https://github.com/users/datasciencemonkey/projects/4).

## Features and to-dos

### The plugin (`plugins/databricks-genie/`)

- ~~Manifest with only the fields Antigravity 2.24.0 reads: name, display name, description, version, logo, three suggested prompts (#11)~~
- ~~A local Genie bridge: a small Python MCP server (standard library only, Python 3.9+) that Antigravity starts, which forwards every call to the Genie One MCP (#11). It began as Node and moved to Python on 2026-10-21, because Antigravity doesn't give plugin servers its bundled Node and every developer machine already has `python3`.~~
- ~~Sign-in through the Databricks CLI login: a fresh token on each request, cached until shortly before it expires, one retry if Genie rejects it, and never stored or printed (#8, #11). This is U2M OAuth (`databricks auth login`), brokered by the CLI. The user chose on 2026-10-21 to keep it; Antigravity's own OAuth waits for a published Databricks app for Antigravity or client ID metadata document support (#22).~~
- ~~No settings to fill in: the workspace comes from the CLI profile, `DEFAULT` unless `DATABRICKS_CONFIG_PROFILE` says otherwise (#21). The user chose to keep this as the design on 2026-10-21.~~
- README: explain how to point the desktop app at another profile (`launchctl setenv DATABRICKS_CONFIG_PROFILE <name>`, then restart the app). The commands are in the README since #33; they still need checking with a second profile the user picks.
- First use with no CLI profile (#33): the bridge answers `initialize` and `tools/list` itself, with a bundled copy of Genie One's four tool schemas, so the tools show up before any sign-in. `genie_connection` reports the status, the CLI profile and the host the bridge will call. `genie_setup` takes the workspace URL the user gives (Databricks workspace domains only) and runs `databricks auth login` in the background, never pointing an existing profile at another workspace. When the CLI is missing, the agent offers Databricks' official installer and runs it only after the user approves. Built test-first; still to be shown working in the CLI, the desktop app and the IDE.
- The workspace line (#33): before each `genie_ask` the agent says "Asking Genie in <host> (CLI profile <profile>)", and the bridge adds the same line to each `genie_ask` result.
- Hardening in the bridge (#33): clear errors for a missing CLI, a cancelled or failed sign-in, HTTP 403 (an IP access list or VPN), HTTP 404 (Genie One not available) and an unreachable workspace; every call ends within 160 s; a new Genie session when Genie ends the old one; no error text that could quote a token.
- Review fixes (#33):
  - A profile that signs in another way (a personal access token or a service principal) is reported as `unsupported_auth`, and `genie_setup` never signs it in again, which would drop its token or secret.
  - Every call has a wall-clock limit, including a reply that trickles in, a slow connect, and a token refresh after a 401.
  - A background sign-in is recorded in the OS temp folder (process id, profile, workspace and start time; no token), so the bridge that `agy -p` starts for the next turn still sees it.
  - The bundled tool list stands in when Genie One answers `tools/list` with an error or nothing.
- ~~Finds the Databricks CLI even when Antigravity starts the bridge with a bare PATH (#21)~~
- ~~An `antigravity-databricks-genie` skill (renamed from `genie` on 2026-10-22, #26): ask, wait for the answer, fetch full results, follow up in the same conversation, put Genie's SQL into code, and fall back to the CLI (#11)~~
- ~~The bridge waits up to 150 s for Genie, so it gives up before Antigravity's 3-minute limit on each tool call, and the skill says to poll again with the same IDs (#26, #28). The wait was 120 s, which timed out a slow poll, then 300 s, which outlasted Antigravity's limit.~~
- ~~An always-on rule that sends data questions to Genie instead of guesswork (#11)~~
- ~~README and logo (#11)~~
- ~~Decide whether to bundle Databricks' published skills (`databricks-core`, `databricks-data-discovery`, `databricks-genie-agents`)~~: decided 2026-10-22, they aren't bundled; the plugin is a standalone Genie connector that doesn't assume other skills are installed (#25)
- Offer the per-space Genie Agent MCP alongside Genie One: a scope decision (#7)
- A read-only "Genie analyst" subagent (#16)
- Rules and hooks for safe data access (#17)
- Install from the GitHub repo URL for teammates or customers; untested while the repo is private (#18)

### The demo

- ~~Genie One accepts a CLI-minted token (direct probe: initialize, list tools, ask, poll) (#21)~~
- ~~Works in the Antigravity CLI: the first-user question answered end to end in about two minutes, with Genie's SQL and links back to Databricks (#12)~~
- ~~Works in the Antigravity desktop app: the plugin's suggested prompt, confirmed by the user, and re-verified with the Python bridge on 2026-10-22 (#12)~~
- Try it in the Antigravity IDE app (untested)
- Lock two or three demo questions, and capture screenshots that are safe to share

### The POV and scope

- ~~Research: Antigravity's extension points (#2), Databricks' Genie MCP and sign-in (#3), plugin ecosystem and listing (#4)~~
- ~~Decisions: the first user (#5) and sign-in (#8)~~
- ~~Internal POV draft, updated with the spike's results (#6)~~
- Your review of the POV, then a Google Doc in your Databricks Drive (#6)
- Scope doc: what ships in v0 vs. later, and sorting the ideas (#7)
- Design doc (#9): skipped when we went straight to the build; close it or write it up after the fact
- Asks of Databricks and Google: an OAuth app or client ID metadata document support, keychain-backed settings, a Discover listing (#22, #18), an Antigravity target in the agent-skills generator (#13), and a CLI installer that installs the plugin (#15)

### Engineering setup

- ~~Project board, labeled issues, AGENTS.md, and the director subagent (#19, #20)~~
- ~~204 tests, written test-first, including the 59-minute token renewal, including repo safety checks and Antigravity's own strict validator (#23)~~
- ~~Pre-push gate, GitHub Actions on every push, and fast-forward merges to `main` with every commit authored and committed as the user (#23)~~
- ~~Restored this machine's Databricks git hooks (secret scanning on commit and push) (#23)~~
- Hardening follow-ups from the code review (#24)

## How it was done

1. **Research, three agents in parallel.** One inspected the installed Antigravity app (its engine binary and built-in docs), one read Databricks' docs and CLI, and one surveyed other plugins and how they're listed. The headline finding: Google's publishing guide differs from the shipped 2.24.0 app in ways that matter. Settings are plain text, `author` and `homepage` are dropped, there's no install-from-URL in the app, and settings sit on each server entry.
2. **Two decisions.** The first user is a developer who needs a governed business number and its SQL in their code. Sign-in goes through a bridge that reuses the Databricks CLI login, because Databricks doesn't offer dynamic client registration (so Antigravity's automatic OAuth can't connect) and a token entered at install would sit in plain text.
3. **A direct probe of Genie One.** A token from `databricks auth token` worked against `https://<workspace>/ai-gateway/mcp-services/system.ai.genie_one_mcp`. The server returns plain JSON with a session ID and offers four tools: `genie_ask`, `genie_poll_response`, `genie_get_query_result` and `genie_cancel_response`. A question against the workspace's sample data came back with an answer and SQL in about 12 seconds.
4. **Test-first build.**
   - Package tests check the manifest fields, the server entry, the skill and rule, and Antigravity's own `agy plugin validate --strict`. That validator caught that settings belong on each server entry, not at the top level as the guide says.
   - Bridge tests run the real bridge against a stand-in CLI and a stand-in Genie server. They cover signing each request, keeping the session ID, passing notifications through, unwrapping event streams, refreshing the token on a 401, clear sign-in errors that never contain the token, building the URL, and finding the CLI.
5. **Install and fix what the spike found.**
   - `agy plugin install` worked, but a server that declares settings stays off until they're filled in through the app, so the plugin dropped its settings and reads everything from the CLI profile.
   - Antigravity registers the server as `databricks-genie_genie` and loads its tools lazily through `call_mcp_tool`, so the skill names that server and the agent calls Genie directly.
6. **Node to Python.** Turning Node off in `PATH` showed that Antigravity doesn't provide it to plugin servers. The bridge was rewritten in Python (standard library only) against the same tests, run under Apple's Python 3.9, and the demo passed again with no Node installed.
7. **The demo.** In the CLI, a headless `agy -p` run needs allow rules for the four Genie tools. In the desktop app, it worked after a restart.
8. **Every change** went the same way: tests, then a commit authored and committed as the user, then the pre-push gate running the suite, then GitHub Actions, then a fast-forward of `main`.
9. **Standalone, and the rename (2026-10-22).** Databricks' agent-skills repo ships no MCP servers. Its Claude bundle installs in Antigravity from its GitHub subfolder URL, but Antigravity 2.24 doesn't read the folder the Databricks CLI installs skills into. So the user chose to keep this plugin a standalone Genie connector, renamed the skill, and raised the bridge's timeout (later lowered to 150 s to stay under Antigravity's 3-minute limit, #28).

### How a question flows

1. The Antigravity agent reads the `antigravity-databricks-genie` skill.
2. It calls `genie_connection` with `call_mcp_tool` on `databricks-genie_genie`, the local bridge, once per conversation. If the CLI profile (`DEFAULT` unless one is set) isn't set up, it asks the user for the workspace URL and calls `genie_setup`, and the user signs in in the browser.
3. Before each question, it tells the user "Asking Genie in <host> (CLI profile <profile>)".
4. The bridge gets a token from `databricks auth token` and posts the call to the Genie One MCP in the workspace.
5. Genie finds the data, runs SQL on a SQL warehouse, and answers.
6. The agent polls until the answer is ready, then shows the numbers, Genie's SQL, and links back to Databricks.

### What changed on this machine

- The Antigravity CLI (`agy`) in `~/.local/bin`, plus the PATH lines its installer added to `~/.profile`, `~/.zprofile` and `~/.zshrc`
- The plugin, installed at `~/.gemini/config/plugins/databricks-genie`
- `~/.gemini/antigravity-cli/settings.json`, which pre-approves the four Genie tools for headless runs
- A backup of Antigravity's `config.json`
- The Databricks git hooks restored (`pre-commit`, `pre-push`), plus this repo's pre-push test gate
