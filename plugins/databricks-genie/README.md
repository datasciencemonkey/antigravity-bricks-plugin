# Databricks Genie for Antigravity

Ask Databricks Genie about your governed data from Antigravity's agent, and turn its answers and SQL into code.

## What's inside

- **A local Genie bridge** (`scripts/genie_bridge.py`), a small Python script that uses only the standard library. It connects Antigravity to the Genie One MCP server in your workspace and signs every request with a fresh token from your Databricks CLI login. No tokens are stored in Antigravity's settings.
- **The `genie` skill.** It teaches the agent how to ask Genie, wait for the answer, and use Genie's SQL.
- **An always-on rule.** It sends data questions to Genie instead of guesswork.

## Before you install

- The Databricks CLI, signed in: `databricks auth login --host https://<workspace>.cloud.databricks.com` (this creates the `DEFAULT` profile)
- Genie One available in your workspace (partner-powered AI features turned on)
- CAN USE on a SQL warehouse, and access to the data you want to ask about
- Python 3.9 or later as `python3`. It's already on developer machines; on a Mac, Apple's command line tools include it.

## Install

```bash
agy plugin install <path-or-git-url-of-this-folder>
```

Restart the Antigravity app (the CLI picks it up on its next run). There's nothing to configure: the plugin uses your `DEFAULT` Databricks CLI profile. To use another profile, set `DATABRICKS_CONFIG_PROFILE` in the environment Antigravity starts from.

## Try it

- "Ask Genie which products drove the most revenue, and show the SQL it ran"
- "Ask Genie to break that down by region"
- "Turn Genie's SQL into a query file in this project"

## Troubleshooting

- **Sign-in errors:** run `databricks auth login --profile <name>` again.
- **The Genie tools don't show up:** check that `python3 --version` works in a terminal, then restart Antigravity.
- **The Databricks CLI isn't found:** the bridge looks on your PATH and in the usual install folders (`/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin`).
