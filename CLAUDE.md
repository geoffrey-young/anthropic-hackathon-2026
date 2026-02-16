# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Claude Code Canary -- an early warning system for Claude Code plugin security.  It intercepts third-party plugin invocations via hooks, injects a structured security audit prompt through exit-2 stderr, and lets Opus analyze every file in the plugin before deciding whether to proceed.  The defense works because the vulnerability it defends against exists: the Claude Code runtime delivers hook stderr content into the LLM's context without filtering or source attribution.

This is a hackathon project, not a production security tool.

## Architecture

The plugin lives at `plugins/plugin-canary/` and follows the Claude Code plugin structure with a manifest at `.claude-plugin/plugin.json`.

**Hook lifecycle (automatic mode):**

1. **SessionStart** (`hooks/discover.py`): Reads `~/.claude/plugins/installed_plugins.json`, walks every file in each plugin, writes a registry to `resources/state.json` with content hashes for change detection.
2. **PreToolUse on Task** (`hooks/gate.py pre`): When Claude invokes a third-party plugin's agent, checks state.  If unaudited, blocks via exit 2 and injects stderr instructions telling Claude to spawn the security-auditor agent.  Sets `audited: true` in state before blocking so the retry passes through.
3. **PostToolUse on Task** (`hooks/gate.py post`): After the audit Task completes, broad text-matches plugin names across all tool_input string fields and records `decision: "approved"`.

**Skill (manual mode):** `/plugin-canary` (`skills/plugin-canary/SKILL.md`) triggers the same pipeline on demand with user-facing decision prompts (approve, reject, keep auditing).

**Agent:** `agents/plugin-canary.md` is the security auditor subagent (pinned to Opus).  Performs a 3-step analysis: threat imagination (before reading code), data/control flow tracing with 10 security heuristics, and hypothesis testing against brainstormed scenarios.

**Shared state:** `core/__init__.py` provides all state I/O, content hashing, file collection, plugin discovery, and the skip list.  State file is `resources/state.json`.

**CLI:** `scripts/manage.py` provides `list`, `status`, `approve`, `reject`, `revoke` subcommands for manual state management.

## Key Design Decisions

- **Python stdlib only.**  No external dependencies.  All scripts use `python3`.
- **`CLAUDE_PLUGIN_ROOT` env var** is set by the Claude Code runtime and used throughout for path resolution.  All scripts do `sys.path.insert(0, os.environ["CLAUDE_PLUGIN_ROOT"])` to import `core`.
- **Atomic state writes** via `save_state()`: writes to `.tmp` then `os.replace()`.
- **Fail-closed:** If state save fails before exit-2 block, the audit may repeat but an unreviewed plugin never passes through.
- **Self-exclusion:** The gate skips its own agent calls (`plugin-canary:*`) to prevent infinite loops.
- **Smart merge on discovery:** Same content hash = keep audit state; changed hash = reset audit; uninstalled = drop.
- **Skip list** in `core/__init__.py` (`SKIP_PATTERNS`): exact-match patterns for plugins that should never be audited (currently just `plugin-canary` itself).
