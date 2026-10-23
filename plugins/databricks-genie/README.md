# Databricks Genie for Antigravity

Ask Databricks Genie about your governed data from Antigravity's agent, and turn its answers and SQL into code.

## What's inside

- **A local Genie bridge** (`scripts/genie_bridge.py`), a small Python script that uses only the standard library. It connects Antigravity to the Genie One MCP server in your workspace and signs every request with a fresh token from your Databricks CLI login. No tokens are stored in Antigravity's settings. It adds two tools of its own: `genie_connection` shows which workspace and Databricks CLI profile Genie uses, and `genie_setup` sets up the Databricks CLI profile on first use.
- **The `antigravity-databricks-genie` skill.** It teaches the agent how to connect, ask Genie, wait for the answer, and use Genie's SQL.
- **An always-on rule.** It sends data questions to Genie instead of guesswork.

## Before you install

- Antigravity: the desktop app 2.25 or later, and/or the `agy` command-line tool. The plugin installs through `agy`, which is a separate download (see Install).
- The Databricks CLI. If it's missing, the agent offers to install it with Databricks' official installer, and only does so once you say yes.
- Genie One available in your workspace (partner-powered AI features turned on)
- CAN USE on a SQL warehouse, and access to the data you want to ask about
- Python 3.9 or later as `python3`. It's already on developer machines; on a Mac, Apple's command line tools include it.
- `git`, because the plugin installs from a repository URL.
- macOS or Linux. Windows is untested.

## Install

1. Install the Antigravity CLI, `agy`, if you don't have it. Open a new terminal afterwards so `agy` is on your PATH.

   ```bash
   curl -fsSL https://antigravity.google/cli/install.sh | bash
   ```

2. Install the plugin.

   ```bash
   agy plugin install https://github.com/datasciencemonkey/antigravity-bricks-plugin
   ```

3. Restart the Antigravity desktop app. The `agy` CLI picks the plugin up on its next run.

There's nothing to configure. The [repository README](https://github.com/datasciencemonkey/antigravity-bricks-plugin#readme) has the same steps, and how to update or remove the plugin.

## First use

The plugin uses your `DEFAULT` Databricks CLI profile. The first time you ask Genie something:

1. The agent checks the connection with `genie_connection`. If the profile is signed in, it goes straight to Genie.
2. If the profile isn't set up, the agent asks for your workspace URL, such as `https://<workspace>.cloud.databricks.com`. It never guesses one, and never picks a profile for you.
3. It calls `genie_setup`, which runs `databricks auth login` for that workspace, and a browser window opens. Sign in with your usual single sign-on and MFA. There's no token to create or paste.
4. When you've signed in, tell the agent in the chat, and it carries on. Before each question, the agent says "Asking Genie in <host> (Databricks CLI profile <profile>)".

When a sign-in expires, the agent signs you in again to the same workspace. `genie_setup` never changes which workspace an existing profile points to. You can also sign in yourself: `databricks auth login --host https://<workspace>.cloud.databricks.com --profile DEFAULT`.

The setup lives only in the Databricks CLI's own config, so the Antigravity CLI and the desktop app use the same profile.

## Use another Databricks CLI profile

Set `DATABRICKS_CONFIG_PROFILE` to the profile's name in the environment Antigravity starts from:

- The Antigravity CLI: run `DATABRICKS_CONFIG_PROFILE=<name> agy ...` to use the profile for that command only.
- The desktop app on a Mac: quit the app fully (Cmd-Q), then open it with `open -a Antigravity --env DATABRICKS_CONFIG_PROFILE=<name>`. The profile applies to that launch only, so no other app on your Mac sees it. To go back to `DEFAULT`, quit the app and open it as usual.

If that profile doesn't exist yet, the agent sets it up the same way on first use.

## Headless runs

`agy -p "<prompt>"` runs without a screen, so it can't ask you to approve a tool, and it refuses any tool call that no rule allows. To let it use Genie, add these allow rules to the `permissions.allow` list in `~/.gemini/antigravity-cli/settings.json`, next to any rules you already have:

```json
{
  "permissions": {
    "allow": [
      "mcp(databricks-genie_genie/genie_connection)",
      "mcp(databricks-genie_genie/genie_ask)",
      "mcp(databricks-genie_genie/genie_poll_response)",
      "mcp(databricks-genie_genie/genie_get_query_result)",
      "mcp(databricks-genie_genie/genie_cancel_response)"
    ]
  }
}
```

Leave `genie_setup` out. It sets up a Databricks CLI profile and opens a browser to sign in, so it should only run in a session where you can see it. Sign in first (see First use), and a headless run finds the profile ready.

## Where it works

The `agy` CLI and the desktop app (2.25 or later) are supported. The Antigravity IDE isn't yet: IDE 2.5.5 doesn't run plugin MCP servers, so the Genie tools don't start there, and turning the plugin on or off in the IDE rewrites its manifest. Use the desktop app or the `agy` CLI for now.

## Try it

- "Ask Genie which products drove the most revenue last quarter, and show the SQL it ran"
- "Ask Genie how last quarter's revenue breaks down by region, and show the SQL it ran"
- "Ask Genie for monthly revenue by product, and save the SQL it ran as a query file in this project"

Each one works on its own in a new chat, so any of them is a good first question.

## Troubleshooting

- **Sign-in errors:** ask the agent to check the Genie connection, and it signs you in again if needed. Or run `databricks auth login --profile <name>` yourself.
- **`unsupported_auth`:** your Databricks CLI profile signs in with a personal access token or a service principal. Genie here signs in with OAuth U2M through the Databricks CLI, so the agent won't use or change that profile. Set `DATABRICKS_CONFIG_PROFILE` to a new profile name and restart Antigravity, and the agent sets that one up.
- **`profile_exists_unreadable`:** the same applies to a profile that `databricks auth profiles` doesn't list because its section has no host, such as an Azure service principal. The agent finds its name in the Databricks config file (`~/.databrickscfg`, or the file `DATABRICKS_CONFIG_FILE` names), reading only section names, and never signs in over it. Use a new profile name, as above.
- **HTTP 403:** the workspace may only accept connections from certain networks (an IP access list). Connect to the VPN if it needs one, and try again.
- **A question times out:** Antigravity ends every tool call after 3 minutes, so the plugin stops waiting on Genie after 150 seconds. Genie keeps working on it after the bridge gives up. If the timeout came while the agent was waiting for an answer, it polls again with the same IDs. If the question itself timed out, its IDs never came back, and asking again starts a duplicate question, so the agent waits a minute or two first.
- **"Genie returned a non-JSON page":** a proxy or a single sign-on page answered instead of Genie. Check the VPN, the proxy and your sign-in, and try again.
- **HTTP 404:** Genie One isn't available in that workspace. A workspace admin may need to turn on partner-powered AI features.
- **The Genie tools don't show up:** check that `python3 --version` works in a terminal, then restart Antigravity.
- **The Databricks CLI isn't found:** the bridge looks on your PATH and in the usual install folders (`/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin`).

## License

MIT. The license text is in [LICENSE](LICENSE), and it travels with the plugin. Databricks and the Databricks logo are trademarks of Databricks, Inc., and the MIT license covers the code, not the marks.
