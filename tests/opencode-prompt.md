I need three quick things done. Work through them in order, don't stop to ask me questions, just do it:

1. Read the OpenCode config file at `~/.config/opencode/opencode.json` and tell me which inference provider is configured (look for the `provider` key).

2. Fetch the README of https://github.com/astral-sh/uv (the top of the file is fine — first ~50 lines) and paste the first paragraph back to me.

3. In a scratch workspace (use $TMPDIR or /tmp, NOT the current project dir), create a new Python project that depends on `requests`, install it, and run a one-liner that prints `requests.__version__`. Report the version you got.

After you're done, print a line:
  TOOL_CALL_COUNT: <N>
where N is how many tool calls you made total.
