# Databricks Genie for Antigravity

Ask Databricks Genie about your governed data from [Google Antigravity](https://antigravity.google), and turn its answers and SQL into code.

Genie finds the right tables in your Databricks workspace, runs SQL on a SQL warehouse, and answers with the numbers and the SQL it ran. This plugin gives Antigravity's agent that ability, so it asks Genie instead of guessing table names or writing SQL from memory.

## What it does

- Gives the Antigravity agent Genie's tools: ask a question in plain language, wait for the answer, fetch complete result rows, follow up in the same conversation, and cancel a slow question.
- Shows where each question goes. Before it asks, the agent says "Asking Genie in <host> (Databricks CLI profile <profile>)".
- Sets itself up the first time you use it. If your Databricks CLI profile isn't set up, the agent asks for your workspace URL and opens a browser window for you to sign in.
- Brings Genie's SQL into your project, for example as a query file.
- Signs in with your own Databricks CLI login. Antigravity and the plugin store no secrets.

The plugin is in [`plugins/databricks-genie/`](plugins/databricks-genie): a small Python script that connects Antigravity to the Genie One MCP server in your workspace, a skill that teaches the agent how to use Genie, and an always-on rule that sends data questions to Genie.

## Requirements

- **Antigravity:** the desktop app 2.25 or later, and/or the `agy` command-line tool. The plugin installs through `agy`, which is a separate download (step 1 below).
- **Databricks:** a workspace where Genie One is available (partner-powered AI features turned on), CAN USE on a SQL warehouse, and access to the data you want to ask about.
- **The Databricks CLI.** If it's missing, the agent offers to install it for you (see [First use](#first-use)).
- **Python 3.9 or later** as `python3`. It's already on most developer machines; on a Mac, Apple's command line tools include it.
- **`git`**, because the plugin installs from this repository's URL.
- **macOS or Linux.** Windows is untested.

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

There's nothing to configure. Check that it's installed with `agy plugin list`.

To update, run the same `agy plugin install https://github.com/datasciencemonkey/antigravity-bricks-plugin` command again, then restart Antigravity. There is no `agy plugin update`: reinstalling replaces the plugin's files exactly, and whether the plugin is turned on or off carries over. To remove it, run `agy plugin uninstall databricks-genie`.

## First use

The plugin uses your `DEFAULT` Databricks CLI profile, unless you [pick another one](#use-another-databricks-cli-profile). The first time you ask Genie something, the agent checks the Databricks CLI and then that profile:

1. If the Databricks CLI isn't installed, the agent offers to install it with Databricks' official installer, and only does so after you say yes. It explains why: the CLI is the safest way to run OAuth U2M (user-to-machine) sign-in from your own machine. You sign in in your browser with your usual single sign-on and MFA, and you never create or paste a token.
2. If the profile is signed in, the agent goes straight to Genie.
3. If the profile isn't set up, the agent asks for your workspace URL, such as `https://<workspace>.cloud.databricks.com`. It never guesses one, and it never picks a profile for you.
4. A browser window opens for you to sign in. When you've signed in, tell the agent in the chat, and it carries on with your question.
5. Before each question, the agent says "Asking Genie in <host> (Databricks CLI profile <profile>)", so you can always see which workspace and profile a question goes to.

When a sign-in expires, the agent signs you in again to the same workspace. The plugin never changes which workspace an existing profile points to. A machine that has never used the Databricks CLI has no `~/.databrickscfg` yet, and that's fine: the first sign-in creates it. You can also sign in yourself first: `databricks auth login --host https://<workspace>.cloud.databricks.com --profile DEFAULT`. To install the Databricks CLI yourself, use `brew install databricks/tap/databricks` on a Mac with Homebrew, or `curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh` on macOS or Linux.

## Try it

Each of these works on its own in a new chat:

- "Ask Genie which products drove the most revenue last quarter, and show the SQL it ran"
- "Ask Genie how last quarter's revenue breaks down by region, and show the SQL it ran"
- "Ask Genie for monthly revenue by product, and save the SQL it ran as a query file in this project"

In chat, `/antigravity-databricks-genie` loads the skill directly.

## Use another Databricks CLI profile

Set `DATABRICKS_CONFIG_PROFILE` to the profile's name in the environment Antigravity starts from:

- **The `agy` CLI:** run `DATABRICKS_CONFIG_PROFILE=<name> agy ...` to use the profile for that command only.
- **The desktop app on a Mac:** quit the app fully (Cmd-Q), then open it with `open -a Antigravity --env DATABRICKS_CONFIG_PROFILE=<name>`. The profile applies to that launch only, so no other app on your Mac sees it. To go back to `DEFAULT`, quit the app and open it as usual.

If that profile doesn't exist yet, the agent sets it up the same way on first use.

Genie is always called at the workspace of the Databricks CLI profile it uses, and the plugin never takes a workspace from your environment, so an exported `DATABRICKS_HOST` doesn't change where it calls. The plugin itself reads only `DATABRICKS_CONFIG_PROFILE` and `DATABRICKS_CONFIG_FILE`. The Databricks CLI it runs gets your environment unchanged and reads its usual `DATABRICKS_*` variables, such as `DATABRICKS_AUTH_STORAGE`, as it does in your terminal. The plugin names the profile on every call, and in Databricks CLI 1.22.1 exporting `DATABRICKS_HOST` for another workspace, `DATABRICKS_TOKEN`, `DATABRICKS_AUTH_TYPE`, a client ID and secret, or `DATABRICKS_ACCOUNT_ID` doesn't redirect it: the CLI still answers with that profile's own sign-in.

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

Leave `genie_setup` out. It sets up a Databricks CLI profile and opens a browser to sign in, so it should only run in a session where you can see it. Sign in first (see [First use](#first-use)), and a headless run finds the profile ready.

## Where it works

- **The `agy` CLI:** supported.
- **The Antigravity desktop app (2.25 or later):** supported.
- **The Antigravity IDE:** not supported yet. IDE 2.5.5 doesn't run plugin MCP servers, so the Genie tools don't start there, and turning the plugin on or off in the IDE rewrites its manifest. This waits on Google ([#30](https://github.com/datasciencemonkey/antigravity-bricks-plugin/issues/30)). Until then, use the desktop app or the `agy` CLI. If you turned the plugin on or off in the IDE, run the install command again to put it back.

## How it works

1. The agent reads the `antigravity-databricks-genie` skill and calls `genie_connection` to check your Databricks CLI profile.
2. It tells you which workspace and profile it will use.
3. The plugin's bridge gets a fresh token from `databricks auth token` and sends your question to the Genie One MCP server in your workspace.
4. Genie finds the data, runs SQL on a SQL warehouse, and answers. The agent polls until the answer is ready, then shows you the numbers, Genie's SQL, and a link back to Databricks.

## Security

- **Sign-in:** today the plugin signs in with your own Databricks CLI OAuth U2M login, so Genie runs under your permissions and sees only what you can see. Service principal sign-in (OAuth M2M) is planned as an opt-in path ([#39](https://github.com/datasciencemonkey/antigravity-bricks-plugin/issues/39)). A Databricks CLI profile that signs in any other way gets a clear `unsupported_auth` message, and one that the CLI doesn't list but that has settings in the Databricks config file gets `profile_exists_unreadable`; the plugin never changes either.
- **No secrets stored:** Antigravity and the plugin store no secrets. There is nothing to fill in, no token in Antigravity's settings, and no password or key in the plugin. The Databricks CLI keeps your sign-in in its own local cache and hands out short-lived tokens (about an hour).
- **Tokens are never printed,** logged or written to disk by the plugin, and the agent is told never to show one.
- **Where your token goes:** only to the workspace of the Databricks CLI profile the plugin uses. The plugin never takes a workspace from your environment, so an exported `DATABRICKS_HOST` can't make it call, or send a token to, another host.
- **One small file:** the only file the plugin itself writes is a short record of a sign-in it started: a process id, the profile, the workspace and the start time, never a token. It's in `~/.cache/antigravity-databricks-genie/`, a folder only you can use, so the next headless `agy -p` run can see that sign-in.
- **Where your data goes:** questions go from your computer to Genie in your own Databricks workspace, and the plugin has no server of its own. Genie's answers come back into your Antigravity conversation, so treat them like anything else you share with Antigravity.

## Troubleshooting

- **Sign-in errors:** ask the agent to check the Genie connection, and it signs you in again if needed. Or run `databricks auth login --profile <name>` yourself.
- **`unsupported_auth`:** your Databricks CLI profile signs in with a personal access token or a service principal. Genie here signs in with OAuth U2M, so the agent won't use or change that profile. Set `DATABRICKS_CONFIG_PROFILE` to a new profile name and restart Antigravity, and the agent sets that one up.
- **`profile_exists_unreadable`:** the same applies to a profile that `databricks auth profiles` doesn't list because it has no host, such as an Azure service principal, when the Databricks config file (`~/.databrickscfg`, or the file `DATABRICKS_CONFIG_FILE` names) holds keys for it. The agent reads only key names, never a value, and never signs in over those keys. Keys that come before the first header belong to the `DEFAULT` profile. A header with no keys under it holds nothing to lose, such as the empty `[DEFAULT]` header that `databricks configure` writes, so the agent sets that profile up as usual. Use a new profile name, as above.
- **HTTP 403:** the workspace may accept connections only from certain networks (an IP access list). Connect to the VPN if it needs one, and try again.
- **HTTP 404:** Genie One isn't available in that workspace. A workspace admin may need to turn on partner-powered AI features.
- **"Genie returned a non-JSON page":** a proxy or a single sign-on page answered instead of Genie. Check the VPN, the proxy and your sign-in, and try again.
- **A question times out:** Antigravity ends every tool call after 3 minutes, so the plugin stops waiting on Genie after 150 seconds. Genie keeps working on it after the bridge gives up. If the timeout came while the agent was waiting for an answer, it polls again with the same IDs. If the question itself timed out, its IDs never came back, and asking again starts a duplicate question, so the agent waits a minute or two first. For a follow-up, which continues an earlier conversation, it first cancels the question still running there. A timeout while fetching a full result or cancelling starts no question, so the agent just tries again. If the plugin couldn't connect to your workspace in time, the request wasn't sent and nothing runs twice: check the network and the VPN, then try again.
- **The Genie tools don't show up:** check that `python3 --version` works in a terminal, then restart Antigravity.
- **The Databricks CLI isn't found:** the plugin looks on your PATH and in the usual install folders (`/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin`).
- **The install fails:** `agy plugin install` needs `git` and a network connection, because it clones this repository.

## What's in this repo

To use the plugin you need only the install URL (see [Install](#install)). The plugin itself is [`plugins/databricks-genie/`](plugins/databricks-genie): a manifest, the bridge script, the skill, the rule and the logo. Everything else is for developing it:

- `tests/` and `tools/` hold the test suite and the repo checks, and `scripts/hooks/` the pre-push gate. `.github/workflows/` runs the suite in CI.
- `pyproject.toml` and `uv.lock` pin what the suite needs.
- `AGENTS.md` and `docs/progress.md` hold the goal, the decisions and the progress notes.

## Development

`uv run --locked pytest` runs the whole suite. It includes Antigravity's own strict validator (so it needs `agy`), an install into a throwaway HOME, and checks that keep secrets, internal references and real workspace hostnames out of the repository. [AGENTS.md](AGENTS.md) holds the goal, the decisions and the working rules. The plugin's own README is [plugins/databricks-genie/README.md](plugins/databricks-genie/README.md).

## License

MIT. See [LICENSE](LICENSE). Databricks and the Databricks logo are trademarks of Databricks, Inc., and the MIT license covers the code, not the marks.
