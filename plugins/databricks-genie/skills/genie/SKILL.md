---
name: genie
description: Ask Databricks Genie questions about business data in a Databricks workspace (revenue, customers, products, which table holds what) and use its answers and SQL in code. Use when the user asks a data question, needs SQL against Databricks tables, or wants numbers from governed data instead of guesses.
---

# Ask Databricks Genie

Genie answers questions about the user's Databricks data. It finds the right tables, runs SQL on a SQL warehouse, and returns the answer with the SQL it ran. Prefer it to guessing table names or writing SQL from memory.

## With the Genie tools (preferred)

1. Call `genie_ask` with the user's question in plain language. For a follow-up, pass the `conversation_id` from the previous answer so Genie keeps the context.
2. `genie_ask` returns a `conversation_id` and a `response_id` with status `in_progress`. Call `genie_poll_response` with both. While it says it's still running, call it again without narrating each poll. Answers usually take 5–30 seconds.
3. The finished answer is Markdown: Genie's reasoning, the SQL it ran, result tables, and an "Explore in Databricks" link. Show the user the key numbers, the SQL, and that link.
4. Result tables in the answer are previews. For complete rows, call `genie_get_query_result` with the `conversation_id`, the `response_id`, and the query's `item_id`.
5. To stop a slow question, call `genie_cancel_response`.

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
