# AGENTS.md

## Goal

Make Databricks Genie easy to use from Google Antigravity by packaging Databricks' managed MCP servers and Agent Skills as an Antigravity plugin.

Deliverables, in order:

1. **POV**: how Databricks should show up in Antigravity through plugins (#6).
2. **Scope**: what a Databricks plugin should contain, Genie first (#7).
3. **POC**: a Genie-only plugin (#11), shown working inside the Antigravity IDE against a real workspace (#12).

**Now:** the MVP below. The POV and scope are parked for later.

Work is tracked as issues on the [project board](https://github.com/users/datasciencemonkey/projects/4).

## MVP success criteria (set by the user, 2026-10-23)

The MVP is done when each of these is shown working:

1. The plugin works from all three Antigravity surfaces: the `agy` CLI, the desktop app and the IDE (#30).
2. On every surface, if the Databricks CLI profile isn't configured, the agent asks the user for their workspace details (#33).
3. It uses those details to set up the Databricks CLI profile (#33).
4. It then uses that Databricks CLI profile to connect to the Genie MCP server (#33).
5. Before each Genie request, it shows the workspace host and the Databricks CLI profile (#33).
6. It's hardened, and it meets the plugin requirements in the publishing guide. Where the guide and the shipped app disagree, it follows the app and says why (#34).

Until then, everything else waits. Issues labeled `later` on the board are parked.

## Status (2026-10-23)

- Done: read the plugin guide (`Antigravity Plugin Marketplace Publishing Guide.pdf`, repo root, git-ignored) (#1); research on Antigravity (#2), Databricks (#3), and the ecosystem (#4); this file (#19); the director subagent (#20); tests and the push gate (#23); the Genie plugin at `plugins/databricks-genie/` (#11); the spike (#21); the demo setup (#10); the upstream check against `databricks/databricks-agent-skills` (#25).
- Working: Genie answers through the plugin in the Antigravity CLI (`agy -p`) and in the desktop app (#12), which was re-verified with the Python bridge on 2026-10-22. The IDE app is untested.
- Decided: the first user and their first question (#5); sign-in (#8, with #14 folded in).
- In progress: the MVP (#33, #34, #30); see MVP success criteria.
- Parked for later (the user, 2026-10-23): the non-Genie work, labeled `later` on the board. That's the POV (#6), scope (#7), the design doc (#9), and #13, #15, #18, #22 and #24.

## What we know

**Antigravity plugins (the guide, corrected against the shipped 2.24.0 app in #2)**

- A plugin is a `plugin.json` plus any of: `skills/<name>/SKILL.md`, `mcp_config.json`, `agents/<name>.md`, `rules/`, `hooks.json`, `README.md`, `assets/`.
- The manifest sits at the root, or at `.antigravity-plugin/plugin.json` when the repo also ships `.claude-plugin/` or `.cursor-plugin/` manifests. Component paths always resolve from the plugin root.
- `name` is kebab-case, must match the folder, and prefixes MCP server names (`<plugin>-<server>`). The 2.24.0 loader reads only `name`, `description`, `displayName`, `logo`, `suggestedPrompts` (up to 3), `version`, `disabled` and `skills`. `author` and `homepage` are silently dropped, despite what the guide says.
- MCP servers are remote (`serverUrl` + `headers`) or local (`command`/`args`/`env`). The guide says Node ships with the app, but plugin servers don't get it: with Node off PATH, a Node server never starts. That's why the Genie bridge is Python.
- `variables` (`name`, `title`, `description`, `link`) are asked for at install and stored as plain text in `~/.gemini/config/config.json`. The guide's `secret`/keychain option doesn't exist in 2.24.0. A server that declares `variables` stays off until the user fills them in through the app, so the Genie plugin declares none (#21).
- Plugin MCP servers register as `<plugin>_<server>` (here `databricks-genie_genie`). Their tools load lazily and are called through `call_mcp_tool`. Headless `agy -p` runs need allow rules such as `mcp(databricks-genie_genie/genie_ask)` in `~/.gemini/antigravity-cli/settings.json`.
- Antigravity cuts each MCP tool call off at 3 minutes ("timed out after 3m0s"), so the bridge's wait has to end sooner (#28). An undocumented per-server `timeoutSeconds` field, seen in the `agy` and language-server binaries and in a public bug report on google-antigravity's CLI, may raise that limit. It's untested for plugin servers.
- `oauth` is `{clientId, clientSecret}`, or automatic discovery with dynamic client registration when it's left out. There's no field for scopes.
- The installed copy is read-only at `~/.gemini/config/plugins/<name>`. Use `${PLUGIN_ROOT}`, `${PLUGIN_DATA}` and `${VAR}`, never absolute paths.
- Skill frontmatter: `name` (matches the folder, becomes `/name` in chat) and `description` (what it does and when to use it).
- Subagent frontmatter: `model` (inherit|flash_lite|flash|pro), plus `enable_write_tools`, `enable_mcp_tools` and `enable_subagent_tools`, all off by default.
- Rules: `rules/AGENTS.md` is always on. `rules/<rule>.md` takes a `trigger` of always_on, model_decision, glob or manual.
- Hooks: PreToolUse, PostToolUse, PreInvocation, PostInvocation and Stop. JSON comes in on stdin and goes out on stdout.
- Managing plugins: in the app, Customizations → Plugins (Discover, Installed) installs by Marketplace ID, and there's no Install from URL. Git URLs and local folders go through `agy plugin install <url|path>` (the CLI is a separate download), then a restart. `/plugin` works in chat.

**From research (#2, #3, #4)**

- Databricks isn't in Antigravity's MCP Store or Marketplace. Discover is curated by Google and fed by `marketplace.json` catalogs; there's no submission process. Plugins ship today as GitHub repos installed with `agy plugin install <url>`.
- Global skills now live in `~/.gemini/config/skills/`. The Databricks CLI still writes to `~/.gemini/antigravity/global_skills`, which Antigravity 2.24 doesn't read (confirmed in #25), so skills it installs don't show up. Our plugin ships its own skill and doesn't depend on those.
- The app's UI includes MCP Apps support, so Genie One's interactive view may render (untested). `agy -p "<prompt>" --output-format stream-json` runs a prompt without the UI, which can script the demo.
- Repos carrying several manifests are normal. Skills must sit one folder deep, `hooks.json` must use Antigravity's own format, and `rules/` is shared with Cursor.
- `databricks/databricks-agent-skills` builds Claude, Codex, Copilot and Cursor bundles from one source, but has no Antigravity target yet. It ships no MCP servers. Its root URL doesn't install with `agy plugin install` ("could not detect plugin structure"). Its Claude bundle does install, from `https://github.com/databricks/databricks-agent-skills/tree/main/plugins/databricks/claude` or a local path, giving 34 skills and no MCP. `agy plugin validate --strict` rejects its Claude and Cursor bundles because they have no native `plugin.json` (#25).
- Genie One MCP is GA at `https://<workspace>/ai-gateway/mcp-services/system.ai.genie_one_mcp` (scope `ai-gateway`). Its tools are `genie_ask`, `genie_poll_response`, `genie_get_query_result`, `genie_cancel_response` and `view_ask`. The older `/api/2.0/mcp/genie` turns off on 2026-10-31. The per-space `/api/2.0/mcp/genie/{space_id}` (scope `genie`) stays.
- Sign-in: Databricks has no dynamic client registration, so Antigravity's automatic OAuth can't connect on its own. Direct OAuth needs an admin-registered OAuth app. PATs work, but would sit in plain text in `config.json`. A local bridge that gets fresh tokens from `databricks auth token` works with no admin step; Databricks' `ug mcp-proxy` does this in Python too.
- The Genie skills (`databricks-data-discovery`, `databricks-genie-agents`) drive Genie through the CLI (`databricks genie ask`, which CLI v1.22.1 has), not through MCP. They should work unchanged in Antigravity if `databricks-core` is installed alongside them and the CLI is logged in. We don't bundle them (see Decisions). They're under the Databricks License, which allows redistribution with the license included and changes marked.
- Antigravity is three products: the desktop app (2.24.0, which holds the plugin engine), the IDE (2.5.5, VS Code-style, Open VSX), and the `agy` CLI (`curl -fsSL https://antigravity.google/cli/install.sh | bash`). Plugins apply to all three.

**This machine**

- Two apps are installed: Antigravity 2.24.0 (`/Applications/Antigravity.app`) and Antigravity IDE 2.5.5 (`/Applications/Antigravity IDE.app`). Config moved to `~/.gemini/config/`, and `mcp_config.json` there is empty. `agy` 1.4.5 is installed at `~/.local/bin/agy`. Its installer put `~/.local/bin` on PATH in `~/.profile`, `~/.zprofile` and `~/.zshrc`, so only shells opened before the install need the full path.
- Databricks CLI v1.22.1 is installed. Its `aitools` installer linked 15 Databricks skills into `~/.gemini/antigravity/global_skills/`, including `databricks-core` and `databricks-genie-agents`. Antigravity 2.24 doesn't read that folder, so none of them show up (confirmed in #25). The CLI's `libs/aitools/agents/agents.go` treats Antigravity as skills-only.

## Decisions

- First user (#5): a developer at a Databricks customer standardized on Google's AI tools. They're building in Antigravity against governed data that their data team already serves through a Genie space. Their first question is a business metric the agent would otherwise guess at, e.g. "Top 10 products by revenue last quarter vs. the prior quarter?", then "By region?", with Genie's SQL going into their code. Business users are out of scope. This is the director's call; the user can override it.
- Sign-in (#8): a Python bridge inside the plugin (standard library only, Python 3.9+) gets a fresh token from `databricks auth token --profile` on each request and forwards calls to the Genie One MCP. The plugin has no settings: the bridge reads the workspace from `databricks auth describe`, using the `DEFAULT` profile or `DATABRICKS_CONFIG_PROFILE`. Only the plugin's own skill (`antigravity-databricks-genie`) ships, and it includes a CLI fallback (`databricks genie ask`); an always-on rule says to prefer the MCP tools. Build the skills first, then the bridge; demo the bridge. No PATs in settings. Admin-registered OAuth is documented, not built. On 2026-10-21 the user confirmed it stays CLI-brokered U2M OAuth (`databricks auth login`).

- Demo setup (#10, decided by the user): the `DEFAULT` CLI profile (a serverless FEVM workspace), with a new Genie space to be created over its sample data. Installing `agy` and running the spike (#21) are approved.
- Standalone connector (#25, #26, decided by the user on 2026-10-22): the plugin is a standalone way to connect Antigravity to Databricks Genie. It doesn't assume other Databricks skills are installed, and it doesn't bundle Databricks' published skills. There's no upstream work in `databricks/databricks-agent-skills`. The skill is renamed `antigravity-databricks-genie`, so its slash command is `/antigravity-databricks-genie`; the MCP server key stays `genie` (`databricks-genie_genie`). The bridge waits up to 150 s for Genie, so it gives up before Antigravity's 3-minute per-call limit (#28). The plugin is version 0.2.1.
- Start with Antigravity (the user, 2026-10-22): other IDE targets, such as one repo for Claude Code, Cursor and Antigravity (#13), are parked.
- MVP (the user, 2026-10-23): the MVP success criteria above are the definition of done. The POV, scope and other non-Genie work wait until they're met.
- First-use setup (#33, the director's design, 2026-10-23): setup goes through the Databricks CLI. The bridge answers `initialize` and `tools/list` itself, so its tools show up before any CLI profile exists. `genie_connection` reports the status, the profile and the host the bridge will call, and `genie_setup` sets up the profile with `databricks auth login` in the background, never pointing an existing profile at another workspace. The CLI config is the only state, so the CLI, the desktop app and the IDE behave the same. MCP elicitation isn't used: Antigravity's handlers for it are undocumented, and headless `agy -p` runs can't answer it. When the CLI is missing, the agent offers Databricks' official installer and runs it only after the user approves, explaining that the CLI is the safest way to run OAuth U2M sign-in from a local machine (the user's call). The bridge reads the workspace host from `databricks auth profiles`, a local read, instead of `auth describe`. `genie_setup` signs a profile in again only if its auth type is `databricks-cli` (U2M). It never touches a profile with another auth type, because `auth login` would rewrite it and drop its token or client secret; it reports `unsupported_auth` instead. A background sign-in is recorded in the OS temp folder, with no token, so the bridge that `agy -p` starts for the next turn still sees it. The plugin is version 0.3.0.

## Open questions

- None right now. New ones go to the director first.

## Working rules

- Questions and dispatch plans go to the `director` subagent first (`.claude/agents/director.md`). It runs distinct to-dos in parallel and sequences the rest, investigates questions with its tools, and says when only the user can decide. Consult it before dispatching any other subagent.
- Every to-do and idea is an issue on the board, assigned to `datasciencemonkey`; move it Todo → In Progress → Done as work happens, and never close one without that assignee. Local helper: `uv run python .omc/scripts/board.py move <issue> "<status>"`.
- Use `uv` to run anything. Every commit is authored and committed as datasciencemonkey@gmail.com.
- Build everything test-first: write a failing test, watch it fail, then write the code. `uv run --locked pytest` is the one test command. It also scans for internal-only references, secrets, real workspace hostnames and wrong commit identities.
- Tests gate every push. Install the pre-push hook once per clone with `uv run python -m tools.install_hooks`. Run the suite before pushing; GitHub Actions runs it again on every push. When both are green, fast-forward `main` with `git push origin HEAD:main`. Never use GitHub's merge buttons, which make GitHub the committer.
- Check PR and issue text before posting: `uv run python -m tools.repo_checks --stdin < text.md`.
- Never relock dependencies through the internal package proxy (the hostname check will flag it). Relock somewhere public PyPI is reachable.
- Never choose a Databricks CLI profile yourself; ask the user. Never print tokens.
- Don't change the user's Antigravity setup (`~/.gemini`, `~/.antigravity`) without asking.
- Research notes are kept in `.omc/research/`, which is git-ignored. Internal-only Databricks material (from Glean or Slack) stays there. Keep it, and customer names, out of GitHub issues and committed files, because this GitHub account is personal, even though the repo is private.
- POV drafts live in `.omc/pov/` (git-ignored). The final version goes into a Google Doc in the user's Databricks Drive after review. GitHub only gets material from public sources.
- Base branches and PRs on `origin/main`. The main checkout's local `main` (7a072aa) is an unrelated empty commit.
- Update this file when research lands or a decision is made.
