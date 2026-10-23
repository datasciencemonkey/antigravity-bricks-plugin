# Databricks Genie for Antigravity

Ask Databricks Genie about your governed data from Antigravity's agent, and turn its answers and SQL into code.

## What's inside

- **A local Genie bridge** (`scripts/genie_bridge.py`), a small Python script that uses only the standard library. It connects Antigravity to the Genie One MCP server in your workspace and signs every request with a fresh token from your Databricks CLI login. No tokens are stored in Antigravity's settings. It adds two tools of its own: `genie_connection` shows which workspace and Databricks CLI profile Genie uses, and `genie_setup` sets up the Databricks CLI profile on first use.
- **The `antigravity-databricks-genie` skill.** It teaches the agent how to connect, ask Genie, wait for the answer, and use Genie's SQL.
- **An always-on rule.** It sends data questions to Genie instead of guesswork.

## Before you install

- The Databricks CLI. If it's missing, the agent offers to install it with Databricks' official installer, and only does so once you say yes.
- Genie One available in your workspace (partner-powered AI features turned on)
- CAN USE on a SQL warehouse, and access to the data you want to ask about
- Python 3.9 or later as `python3`. It's already on developer machines; on a Mac, Apple's command line tools include it.

## Install

```bash
agy plugin install <path-or-git-url-of-this-folder>
```

Restart the Antigravity app (the CLI picks it up on its next run). There's nothing to configure.

## First use

The plugin uses your `DEFAULT` Databricks CLI profile. The first time you ask Genie something:

1. The agent checks the connection with `genie_connection`. If the profile is signed in, it goes straight to Genie.
2. If the profile isn't set up, the agent asks for your workspace URL, such as `https://<workspace>.cloud.databricks.com`. It never guesses one, and never picks a profile for you.
3. It calls `genie_setup`, which runs `databricks auth login` for that workspace, and a browser window opens. Sign in with your usual single sign-on and MFA. There's no token to create or paste.
4. Once you're signed in, Genie answers. Before each question, the agent says where it's asking, for example "Asking Genie in <host> (Databricks CLI profile DEFAULT)".

When a sign-in expires, the agent signs you in again to the same workspace. `genie_setup` never changes which workspace an existing profile points to. You can also sign in yourself: `databricks auth login --host https://<workspace>.cloud.databricks.com` creates the `DEFAULT` profile.

The setup lives only in the Databricks CLI's own config, so the Antigravity CLI, the desktop app and the IDE all use the same profile.

## Use another Databricks CLI profile

Set `DATABRICKS_CONFIG_PROFILE` to the profile's name in the environment Antigravity starts from:

- The Antigravity CLI: run `export DATABRICKS_CONFIG_PROFILE=<name>` in your shell before `agy`.
- The desktop app or the IDE on a Mac: run `launchctl setenv DATABRICKS_CONFIG_PROFILE <name>`, then quit the app fully (Cmd-Q) and open it again. This lasts until you log out. To go back to `DEFAULT`, run `launchctl unsetenv DATABRICKS_CONFIG_PROFILE` and restart the app.

If that profile doesn't exist yet, the agent sets it up the same way on first use.

## Try it

- "Ask Genie which products drove the most revenue, and show the SQL it ran"
- "Ask Genie to break that down by region"
- "Turn Genie's SQL into a query file in this project"

## Troubleshooting

- **Sign-in errors:** ask the agent to check the Genie connection, and it signs you in again if needed. Or run `databricks auth login --profile <name>` yourself.
- **A profile that signs in with a personal access token or a service principal:** Genie here signs in with OAuth U2M through the Databricks CLI, so the agent won't use or change that profile. Set `DATABRICKS_CONFIG_PROFILE` to a new profile name and restart Antigravity, and the agent sets that one up.
- **HTTP 403:** the workspace may only accept connections from certain networks (an IP access list). Connect to the VPN if it needs one, and try again.
- **HTTP 404:** Genie One isn't available in that workspace. A workspace admin may need to turn on partner-powered AI features.
- **The Genie tools don't show up:** check that `python3 --version` works in a terminal, then restart Antigravity.
- **The Databricks CLI isn't found:** the bridge looks on your PATH and in the usual install folders (`/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin`).
