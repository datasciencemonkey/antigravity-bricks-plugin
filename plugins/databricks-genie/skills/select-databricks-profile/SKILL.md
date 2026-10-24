---
name: select-databricks-profile
description: Ask Databricks Genie in another Databricks workspace, by its Databricks CLI profile, for the part of a question the user asked about there, without changing the default profile. Use when the user names a workspace or a profile for a question, says to use one from now on, or asks a question whose parts live in different workspaces. Matches the workspace to a profile and asks when that isn't clear, sets up a profile for a workspace that has none yet, and splits a question that spans workspaces.
---

# Ask Genie in another workspace

The `antigravity-databricks-genie` skill covers asking Genie. This one covers asking in a workspace other than the default: pass that workspace's Databricks CLI profile as `profile` to the Genie tools on the MCP server `databricks-genie_genie` (`genie_ask`, `genie_poll_response`, `genie_get_query_result`, `genie_cancel_response` and `genie_connection`), and to `genie_setup` for a new profile. Leave `profile` out for the default.

## The default never changes

Genie's default is the `DEFAULT` Databricks CLI profile, or the one Antigravity was started with. Nothing in this skill changes it, and a question that names no workspace goes there.

## A workspace named for one question

When the user names a workspace or a profile for a question, or for part of one ("in profile B, …", "… and in my sales workspace"), only that part goes there. The rest of the question, and every later one, goes to the default.

## A workspace to use from now on

When the user says to use a workspace or a profile from now on ("use workspace B from now on", "switch to B"), pass that profile on every Genie call for the rest of this chat, until they switch back ("back to my default", "use DEFAULT"). Then leave `profile` out again. That changes nothing else: not the default profile itself, not other chats, and not new sessions. The workspace line still comes before every question, so the user can see which workspace is in use. Without "from now on", a workspace named on one question applies only to that question.

## Find the profile

1. Call `genie_connection`. Its `profiles` lists the `name`, `host` and `auth_type` of each Databricks CLI profile.
2. Match what the user named against those names and hosts, and use a profile only when exactly one matches. When more than one could match, or none does, ask the user which one they mean, and show them the candidates. Never pick a profile on your own.
3. Genie here signs in with OAuth U2M only, so only a profile whose `auth_type` is `databricks-cli` works.
4. Call `genie_connection` with that `profile`, and follow its `next_step`.

## A workspace with no profile yet

When no profile points to the workspace the user named, offer to set one up. Ask for its workspace URL, such as `https://<workspace>.cloud.databricks.com`, and never guess one, and ask the user to pick a name for the new profile. Tell them a browser window will open for them to sign in, as on first use. Then call `genie_setup` with `workspace_url` and `profile` set to the name they picked, and follow the sign-in with `genie_connection` and the same `profile`. `genie_setup` never changes a profile that already exists.

## A question that spans workspaces

When the parts of a question live in different workspaces ("revenue from the default, pipeline from the sales workspace"):

1. Split it into one question per workspace.
2. Rewrite each part for the workspace that holds its data, so that it makes sense on its own.
3. Ask each part in its own workspace, with that workspace's `profile`, or none for the default.
4. Combine the answers, and say which numbers came from which workspace and Databricks CLI profile.

## Before each question

Before each `genie_ask`, tell the user "Asking Genie in <host> (Databricks CLI profile <profile>)", with the host and the profile of the workspace that part goes to. The bridge ends each `genie_ask` result with a matching "Asked Genie in" line.

## Follow-ups and polls

A conversation stays in the workspace where it started. Pass the same `profile` on every call in it, on every turn: each follow-up `genie_ask`, `genie_poll_response`, `genie_get_query_result` and `genie_cancel_response`. The bridge remembers a conversation's profile only while it runs, and a headless `agy -p` run starts a new bridge each turn. The bridge refuses a call that names another profile for a conversation it knows.
