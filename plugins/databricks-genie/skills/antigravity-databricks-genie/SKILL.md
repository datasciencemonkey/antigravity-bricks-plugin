---
name: antigravity-databricks-genie
description: Ask Databricks Genie questions about business data in a Databricks workspace (revenue, customers, products, which table holds what) and use its answers and SQL in code. Use when the user asks a data question, needs SQL against Databricks tables, or wants numbers from governed data instead of guesses. Also sets up the Databricks CLI sign-in Genie needs on first use.
---

# Ask Databricks Genie

Genie answers questions about the user's Databricks data. It finds the right tables, runs SQL on a SQL warehouse, and returns the answer with the SQL it ran. Prefer it to guessing table names or writing SQL from memory.

In Antigravity these tools are on the MCP server `databricks-genie_genie`. Call them with `call_mcp_tool`, passing that server name, the tool name, and the arguments below. Their arguments are all listed here, so there's no need to look up their schemas first.

To ask in another workspace, for a question, part of one, or from now on, follow the `select-databricks-profile` skill. It passes that workspace's Databricks CLI profile as `profile`, and the default profile never changes.

## Check the connection first

Call `genie_connection` (no arguments) once per conversation, before the first question. It returns the `status`, the Databricks CLI `profile`, the workspace `host` Genie will use, and a `next_step`. By status:

- `ready`: go ahead and ask.
- `not_configured`: the Databricks CLI profile isn't set up yet. Ask the user for their Databricks workspace URL, such as `https://<workspace>.cloud.databricks.com`, and never guess one. The first time you ask, explain in a sentence or two: Genie signs in through the Databricks CLI, the safest way to run OAuth U2M (user-to-machine) sign-in from their own machine; a browser window will open where they sign in with their usual single sign-on and MFA, and they never create or paste a token. Then call `genie_setup` with the URL they give you. If `profiles` lists other Databricks CLI profiles, mention that they can use one of those instead by setting `DATABRICKS_CONFIG_PROFILE` to its name and restarting Antigravity.
- `login_in_progress`: a browser window is open for the user to sign in. Ask them to finish there, then call `genie_connection` again. A sign-in left unfinished gives up after 5 minutes.
- `login_needed`: the sign-in has expired. Tell the user a browser window will open to sign in again, then call `genie_setup` with the workspace URL from the `next_step`.
- `login_failed`: the sign-in was cancelled, timed out or failed. Ask the user whether to try again, and if so call `genie_setup` again.
- `cli_missing`: the Databricks CLI isn't installed. Offer to install it with Databricks' official installer, and run the installer only after the user approves; never install it silently. Ask like this: "Genie signs in through the Databricks CLI. It's the safest way to run OAuth U2M (user-to-machine) sign-in from your own machine. You sign in in your browser with your usual single sign-on and MFA, and never create or paste a token. The CLI keeps your sign-in in its own local cache and hands out short-lived tokens (about an hour). Nothing secret is stored in Antigravity's settings or in this plugin, and Genie only sees what your own Databricks permissions allow. May I install it with Databricks' official installer: `<command>`?" Use the command for their system: on a Mac with Homebrew, `brew install databricks/tap/databricks`; on macOS or Linux without Homebrew, `curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh`; on Windows, `winget install Databricks.DatabricksCLI` (this plugin is untested on Windows). If the installer says `/usr/local/bin` isn't writable, ask the user to run it again themselves with `sudo` in a terminal. Once it's installed, call `genie_connection` again and carry on with the setup.
- `unsupported_auth`: the Databricks CLI profile signs in some other way, such as a personal access token or a service principal, and Genie here signs in with OAuth U2M. Never change or sign in that profile. Explain this, and suggest a separate U2M profile: the user sets `DATABRICKS_CONFIG_PROFILE` to a new profile name and restarts Antigravity, then you set it up as for `not_configured`.
- `profile_exists_unreadable`: the Databricks config file already has settings for this Databricks CLI profile, but the CLI doesn't list it (usually because it has no host, as with an Azure service principal that sets `azure_workspace_resource_id`), or the file can't be read. Genie can't tell how that profile signs in, and signing it in again would rewrite it and drop its secret. Never change or sign in that profile. Explain this, and suggest a separate profile: the user sets `DATABRICKS_CONFIG_PROFILE` to a new profile name and restarts Antigravity, then you set it up as for `not_configured`.

Never pick a profile or a workspace URL for the user. `genie_setup` never changes a profile that's already set up for another workspace.

## Ask Genie

1. Before each `genie_ask`, tell the user "Asking Genie in <host> (Databricks CLI profile <profile>)", with the host and profile from `genie_connection`. The bridge ends each `genie_ask` result with a matching line, "Asked Genie in <host> (Databricks CLI profile <profile>)".
2. Call `genie_ask` with the user's question in plain language. For a follow-up, pass the `conversation_id` from the previous answer so Genie keeps the context. If `genie_ask` itself times out, Genie may still be working on the question, but its IDs never came back, so asking again starts a duplicate question: for a follow-up, call `genie_cancel_response` with its `conversation_id` first, then wait a minute or two and ask once more.
3. `genie_ask` returns a `conversation_id` and a `response_id` with status `in_progress`. Call `genie_poll_response` with both. While it says it's still running, call it again without narrating each poll. Answers usually take 5–30 seconds. If a poll call times out, poll again with the same `conversation_id` and `response_id` instead of asking the question again.
4. The finished answer is Markdown: Genie's reasoning, the SQL it ran, result tables, and an "Explore in Databricks" link. Show the user the key numbers, the SQL, and that link.
5. Result tables in the answer are previews. For complete rows, call `genie_get_query_result` with the `conversation_id`, the `response_id`, and the query's `item_id`.
6. To stop a slow question, call `genie_cancel_response`.

If a Genie call says Genie isn't connected yet, follow the step it gives, as above.

## Putting Genie's answer into code

- Use Genie's SQL as written; it has already run. Keep table names exactly as Genie wrote them, backticks included.
- Note in a comment that the query came from Genie.
- Don't write SQL that changes data (INSERT, UPDATE, DELETE, or DDL) from a Genie answer unless the user asks for it.

## If the Genie tools aren't available

The Databricks CLI reaches the same Genie backend:

```bash
databricks genie ask -s <session> "<question>" --include-sql --profile <profile>
```

Reuse the same `-s` session label for follow-ups. If it fails with a sign-in error, ask the user to run `databricks auth login --profile <profile>`.

## When Genie can't answer

If Genie says it couldn't find relevant data, don't retry the same question. Tell the user what Genie said and ask which table or dataset they mean.
