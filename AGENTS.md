# AGENTS.md

## Goal

Make Databricks Genie easy to use from Google Antigravity by packaging Databricks' managed MCP servers and Agent Skills as an Antigravity plugin.

Deliverables, in order:

1. **POV**: how Databricks should show up in Antigravity through plugins (#6).
2. **Scope**: what a Databricks plugin should contain, Genie first (#7).
3. **POC**: a Genie-only plugin (#11), shown working inside the Antigravity IDE against a real workspace (#12).

Work is tracked as issues on the [project board](https://github.com/users/datasciencemonkey/projects/4).

## Status (2026-10-20)

- Done: read the plugin guide, `Antigravity Plugin Marketplace Publishing Guide.pdf` (repo root, untracked).
- In progress: research on Antigravity (#2), Databricks Genie MCP, sign-in and skills (#3), and ecosystem and listing (#4).
- Not started: POV, scope, design doc, build, demo. Building starts after the design doc (#9) is approved.

## What we know

**Antigravity plugins (from the guide)**

- A plugin is a `plugin.json` plus any of: `skills/<name>/SKILL.md`, `mcp_config.json`, `agents/<name>.md`, `rules/`, `hooks.json`, `README.md`, `assets/`.
- The manifest sits at the root, or at `.antigravity-plugin/plugin.json` when the repo also ships `.claude-plugin/` or `.cursor-plugin/` manifests. Component paths always resolve from the plugin root.
- `name` is kebab-case, must match the folder, and prefixes MCP server names (`<plugin>-<server>`). Marketplace listings also need `displayName`, a square `logo`, up to 3 `suggestedPrompts`, a semver `version`, and `author`.
- MCP servers are remote (`serverUrl` + `headers`) or local (`command`/`args`/`env`). Entries in the MCP file's `variables` are asked for at install, and `secret: true` values go to the OS keychain. Node/npx ship with the app.
- The installed copy is read-only at `~/.gemini/config/plugins/<name>`. Use `${PLUGIN_ROOT}`, `${PLUGIN_DATA}` and `${VAR}`, never absolute paths.
- Skill frontmatter: `name` (matches the folder, becomes `/name` in chat) and `description` (what it does and when to use it).
- Subagent frontmatter: `model` (inherit|flash_lite|flash|pro), plus `enable_write_tools`, `enable_mcp_tools` and `enable_subagent_tools`, all off by default.
- Rules: `rules/AGENTS.md` is always on. `rules/<rule>.md` takes a `trigger` of always_on, model_decision, glob or manual.
- Hooks: PreToolUse, PostToolUse, PreInvocation, PostInvocation and Stop. JSON comes in on stdin and goes out on stdout.
- Managing plugins: Customizations → Plugins (Discover, Installed, Install from URL), `/plugin` in chat, or `agy plugin validate|install|list|enable|disable|uninstall|import`.
- Gaps: the guide's `oauth` section is blank, and it doesn't say how to get listed in Discover.

**This machine**

- Antigravity 2.0.6 is at `/Applications/Antigravity.app`. Its config moved to `~/.gemini/config/`, and `mcp_config.json` there is empty. `agy` is not on PATH.
- Databricks CLI v1.19.0 is installed. 9 Databricks skills are linked into `~/.gemini/antigravity/global_skills/`, apparently by the CLI, and none of them cover Genie. Genie skills exist at `~/.databricks/aitools/skills/databricks-data-discovery` and in the Claude Code Databricks plugin (`databricks-genie-agents`).

## Open questions

- Who is the first user, and what do they ask Genie first (#5)? Working guess: a developer or data person in Antigravity who wants Genie answers without leaving the IDE.
- How do users sign in: Antigravity OAuth, a PAT in the keychain, or reusing the Databricks CLI login (#8)?
- Which workspace and Genie space do we demo on (#10)?

## Working rules

- Every to-do and idea is an issue on the board, assigned to `datasciencemonkey`; move it Todo → In Progress → Done as work happens, and never close one without that assignee. Local helper: `uv run python .omc/scripts/board.py move <issue> "<status>"`.
- Use `uv` to run anything. Every commit is authored and committed as datasciencemonkey@gmail.com.
- Never choose a Databricks CLI profile yourself; ask the user. Never print tokens.
- Don't change the user's Antigravity setup (`~/.gemini`, `~/.antigravity`) without asking.
- Research notes are kept in `.omc/research/`, which is local and uncommitted and may include internal-only material. The repo is private.
- Update this file when research lands or a decision is made.
