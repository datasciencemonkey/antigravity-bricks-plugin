# AGENTS.md

## Goal

Make Databricks Genie easy to use from Google Antigravity by packaging Databricks' managed MCP servers and Agent Skills as an Antigravity plugin.

Deliverables, in order:

1. **POV**: how Databricks should show up in Antigravity through plugins (#6).
2. **Scope**: what a Databricks plugin should contain, Genie first (#7).
3. **POC**: a Genie-only plugin (#11), shown working inside the Antigravity IDE against a real workspace (#12).

Work is tracked as issues on the [project board](https://github.com/users/datasciencemonkey/projects/4).

## Status (2026-10-20)

- Done: read the plugin guide (`Antigravity Plugin Marketplace Publishing Guide.pdf`, repo root, git-ignored) (#1); research on Antigravity (#2), Databricks (#3), and the ecosystem (#4); this file (#19); the director subagent (#20).
- Decided: the first user and their first question (#5); sign-in (#8, with #14 folded in).
- In progress: nothing yet; the POV (#6) is next.
- Next, in order: POV (#6) → scope (#7, which also sorts ideas #13–#18 and #22 into v0, later, or drop) → demo workspace (#10, the user decides) → spike (#21) → design doc (#9) → build (#11) → demo (#12). Building starts after the design doc is approved.

## What we know

**Antigravity plugins (the guide, corrected against the shipped 2.24.0 app in #2)**

- A plugin is a `plugin.json` plus any of: `skills/<name>/SKILL.md`, `mcp_config.json`, `agents/<name>.md`, `rules/`, `hooks.json`, `README.md`, `assets/`.
- The manifest sits at the root, or at `.antigravity-plugin/plugin.json` when the repo also ships `.claude-plugin/` or `.cursor-plugin/` manifests. Component paths always resolve from the plugin root.
- `name` is kebab-case, must match the folder, and prefixes MCP server names (`<plugin>-<server>`). The 2.24.0 loader reads only `name`, `description`, `displayName`, `logo`, `suggestedPrompts` (up to 3), `version`, `disabled` and `skills`. `author` and `homepage` are silently dropped, despite what the guide says.
- MCP servers are remote (`serverUrl` + `headers`) or local (`command`/`args`/`env`). Node/npx ship with the app.
- `variables` (`name`, `title`, `description`, `link`) are asked for at install and stored as plain text in `~/.gemini/config/config.json`. The guide's `secret`/keychain option doesn't exist in 2.24.0. (This comes from the app's data definitions and stays unverified until the spike, #21.)
- `oauth` is `{clientId, clientSecret}`, or automatic discovery with dynamic client registration when it's left out. There's no field for scopes.
- The installed copy is read-only at `~/.gemini/config/plugins/<name>`. Use `${PLUGIN_ROOT}`, `${PLUGIN_DATA}` and `${VAR}`, never absolute paths.
- Skill frontmatter: `name` (matches the folder, becomes `/name` in chat) and `description` (what it does and when to use it).
- Subagent frontmatter: `model` (inherit|flash_lite|flash|pro), plus `enable_write_tools`, `enable_mcp_tools` and `enable_subagent_tools`, all off by default.
- Rules: `rules/AGENTS.md` is always on. `rules/<rule>.md` takes a `trigger` of always_on, model_decision, glob or manual.
- Hooks: PreToolUse, PostToolUse, PreInvocation, PostInvocation and Stop. JSON comes in on stdin and goes out on stdout.
- Managing plugins: in the app, Customizations → Plugins (Discover, Installed) installs by Marketplace ID, and there's no Install from URL. Git URLs and local folders go through `agy plugin install <url|path>` (the CLI is a separate download), then a restart. `/plugin` works in chat.

**From research (#2, #3, #4)**

- Databricks isn't in Antigravity's MCP Store or Marketplace. Discover is curated by Google and fed by `marketplace.json` catalogs; there's no submission process. Plugins ship today as GitHub repos installed with `agy plugin install <url>`.
- Global skills now live in `~/.gemini/config/skills/`. The Databricks CLI still writes to `~/.gemini/antigravity/global_skills`, which 2.x no longer reads, so skills it installs are likely invisible. Bundling the skills in the plugin avoids this.
- The app's UI includes MCP Apps support, so Genie One's interactive view may render (untested). `agy -p "<prompt>" --output-format stream-json` runs a prompt without the UI, which can script the demo.
- Repos carrying several manifests are normal. Skills must sit one folder deep, `hooks.json` must use Antigravity's own format, and `rules/` is shared with Cursor.
- `databricks/databricks-agent-skills` builds Claude, Codex, Copilot and Cursor bundles from one source, but has no Antigravity target yet.
- Genie One MCP is GA at `https://<workspace>/ai-gateway/mcp-services/system.ai.genie_one_mcp` (scope `ai-gateway`). Its tools are `genie_ask`, `genie_poll_response`, `genie_get_query_result`, `genie_cancel_response` and `view_ask`. The older `/api/2.0/mcp/genie` turns off on 2026-10-31. The per-space `/api/2.0/mcp/genie/{space_id}` (scope `genie`) stays.
- Sign-in: Databricks has no dynamic client registration, so Antigravity's automatic OAuth can't connect on its own. Direct OAuth needs an admin-registered OAuth app. PATs work, but would sit in plain text in `config.json`. A local bridge that gets fresh tokens from `databricks auth token` works with no admin step; Databricks' `ug mcp-proxy` does this in Python, and Node ships with Antigravity.
- The Genie skills (`databricks-data-discovery`, `databricks-genie-agents`) drive Genie through the CLI (`databricks genie ask`, which CLI v1.19.0 has), not through MCP. They should work unchanged in Antigravity with `databricks-core` bundled and the CLI logged in. They're under the Databricks License, which allows redistribution with the license included and changes marked.
- Antigravity is three products: the desktop app (2.24.0, which holds the plugin engine), the IDE (2.5.5, VS Code-style, Open VSX), and the `agy` CLI (`curl -fsSL https://antigravity.google/cli/install.sh | bash`). Plugins apply to all three.

**This machine**

- Two apps are installed: Antigravity 2.24.0 (`/Applications/Antigravity.app`) and Antigravity IDE 2.5.5 (`/Applications/Antigravity IDE.app`). Config moved to `~/.gemini/config/`, and `mcp_config.json` there is empty. `agy` is not on PATH.
- Databricks CLI v1.19.0 is installed. Its `aitools` installer linked 9 Databricks skills into `~/.gemini/antigravity/global_skills/`; the CLI's `libs/aitools/agents/agents.go` treats Antigravity as skills-only. None of the 9 cover Genie. Genie skills exist at `~/.databricks/aitools/skills/databricks-data-discovery` and in the Claude Code Databricks plugin (`databricks-genie-agents`).

## Decisions

- First user (#5): a developer at a Databricks customer standardized on Google's AI tools. They're building in Antigravity against governed data that their data team already serves through a Genie space. Their first question is a business metric the agent would otherwise guess at, e.g. "Top 10 products by revenue last quarter vs. the prior quarter?", then "By region?", with Genie's SQL going into their code. Business users are out of scope. This is the director's call; the user can override it.
- Sign-in (#8): a Node bridge inside the plugin gets a fresh token from `databricks auth token --profile` on each request and forwards calls to the Genie One MCP. Settings hold only the workspace host and CLI profile. The Genie skills ship alongside as know-how and a CLI fallback, and a rule says to prefer the MCP tools. Build the skills first, then the bridge; demo the bridge. No PATs in settings. Admin-registered OAuth is documented, not built.

- Demo setup (#10, decided by the user): the `DEFAULT` CLI profile (a serverless FEVM workspace), with a new Genie space to be created over its sample data. Installing `agy` and running the spike (#21) are approved.

## Open questions

- None right now. New ones go to the director first.

## Working rules

- Questions and dispatch plans go to the `director` subagent first (`.claude/agents/director.md`). It keeps the work on one to-do at a time, investigates questions with its tools, and says when only the user can decide. Consult it before dispatching any other subagent.
- Every to-do and idea is an issue on the board, assigned to `datasciencemonkey`; move it Todo → In Progress → Done as work happens, and never close one without that assignee. Local helper: `uv run python .omc/scripts/board.py move <issue> "<status>"`.
- Use `uv` to run anything. Every commit is authored and committed as datasciencemonkey@gmail.com.
- Never choose a Databricks CLI profile yourself; ask the user. Never print tokens.
- Don't change the user's Antigravity setup (`~/.gemini`, `~/.antigravity`) without asking.
- Research notes are kept in `.omc/research/`, which is git-ignored. Internal-only Databricks material (from Glean or Slack) stays there. Keep it, and customer names, out of GitHub issues and committed files, because this GitHub account is personal, even though the repo is private.
- POV drafts live in `.omc/pov/` (git-ignored). The final version goes into a Google Doc in the user's Databricks Drive after review. GitHub only gets material from public sources.
- Base branches and PRs on `origin/main`. The main checkout's local `main` (7a072aa) is an unrelated empty commit.
- Update this file when research lands or a decision is made.
