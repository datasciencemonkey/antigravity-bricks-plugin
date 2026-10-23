# Databricks Genie

- For questions about business data in Databricks (metrics, revenue, customers, which table holds what), ask Genie with `genie_ask` and `genie_poll_response` instead of guessing table names or writing SQL from memory. The `antigravity-databricks-genie` skill has the details.
- Call `genie_connection` once per conversation, before the first Genie question, and follow its `next_step`. Before each `genie_ask`, tell the user "Asking Genie in <host> (Databricks CLI profile <profile>)".
- If the Databricks CLI profile isn't set up, ask the user for their Databricks workspace URL, tell them a browser window will open for them to sign in, then call `genie_setup` with it. Never pick a profile or a workspace URL for the user.
- If the Databricks CLI is missing, offer to install it with Databricks' official installer, explaining that it's the safest way to run OAuth U2M sign-in from their own machine, and run the installer only after the user approves.
- When you use a Genie answer, show its SQL and include the "Explore in Databricks" link it returns.
- Never print, log, or write Databricks tokens.
