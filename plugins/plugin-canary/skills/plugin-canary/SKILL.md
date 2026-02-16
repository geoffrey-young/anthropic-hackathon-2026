---
name: plugin-canary
description: Early warning system for Claude Code plugin security. Detects suspicious patterns and malicious behavior in installed plugins through automated hooks and on-demand analysis.
model: opus
disable-model-invocation: true
user-invocable: true
context: fork
allowed-tools:
  - Read
  - Glob
  - Grep
  - Task
  - AskUserQuestion
  - "Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage.py:*)"
---

# Plugin Security Audit

Audit an installed Claude Code plugin for security vulnerabilities,
malicious behavior, and suspicious patterns.

## Step 1: Find the Plugin

Extract the plugin name from the user's input. If no name was provided,
ask the user which plugin they want to audit.

Find the plugin's files. Try these two approaches in order:

**Approach 1 -- Check auditor state:**

Use the Read tool to read `${CLAUDE_PLUGIN_ROOT}/resources/state.json`.
This file contains a `plugins` dict keyed by `name@source` where each
value has a `files` array of absolute paths. Match the user's input
against plugin keys (case-insensitive substring match on the name
before the @).

**Approach 2 -- Check installed plugins registry:**

If state.json is empty or the plugin is not found, use the Read tool
to read `~/.claude/plugins/installed_plugins.json`. This file has a
`plugins` dict keyed by `name@source` where each value is an array
with an object containing `installPath`. Match the user's input, then
use Glob to find all files under the install path (skip __pycache__).

**Handle match results:**

- No match: tell the user and stop.
- Multiple matches: list them and ask the user to be specific.
- Exactly one match: proceed to Step 2.

## Step 2: Launch Security Audit

Use the Task tool with these exact parameters:
- subagent_type: "plugin-canary:plugin-canary"
- model: "opus"
- prompt: Include the full plugin key (e.g.,
  "code-simplifier@claude-plugins-official") and the complete list
  of file paths to review.

Wait for the agent to complete and return its findings.

## Step 3: Present Findings and Ask User Decision

Present the agent's findings to the user. Then use AskUserQuestion
based on the agent's verdict.

**If the agent concluded SAFE:**

- header: "Decision"
- question: "The audit found this plugin safe. Save this decision so
  it won't be audited automatically next time?"
- options:
  - "Approve (skip future audits)" -- writes approved to state
  - "Don't save" -- no state change

**If the agent concluded DANGEROUS or UNCERTAIN:**

- header: "Decision"
- question: "The audit flagged concerns with this plugin. What would
  you like to do?"
- options:
  - "Accept risk (skip future audits)" -- writes approved to state
  - "Block (mark as dangerous)" -- writes rejected to state
  - "Keep, audit again next time" -- no state change

In both dangerous/uncertain cases, also show this informational text:
"To remove this plugin: `claude plugin uninstall <plugin-name>`"

Do NOT offer to run the uninstall command.

## Step 4: Write Decision

If the user chose "Accept risk", run:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage.py approve <plugin-key>
```

If the user chose "Block (mark as dangerous)", run:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage.py reject <plugin-key>
```

If the state file is empty, tell the user the decision could not be
saved but the audit result is still valid.
