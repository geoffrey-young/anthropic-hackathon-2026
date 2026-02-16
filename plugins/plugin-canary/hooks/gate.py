#!/usr/bin/env python3
"""PreToolUse + PostToolUse gate for plugin-canary.

Combined hook script handling both halves of the gating lifecycle:
  - PreToolUse ("pre"): checks state, injects audit instructions via
    stderr (exit 2), or allows silently (exit 0).
  - PostToolUse ("post"): records user approval in state (always exit 0).

On PreToolUse, the check order is:
  1. Our own agent (plugin-canary[:*]) -> allow (prevent infinite loop).
  2. Unknown plugin agent not in state -> re-discover, then recheck.
  3. Has decision "approved" -> allow.
  4. Has decision "rejected" -> block permanently.
  5. audited == true (retry after injection) -> allow.
  6. Otherwise -> inject audit prompt via exit 2.

Input: Hook JSON on stdin (from Claude Code hooks system).

Usage from plugin.json hooks:
    python3 hooks/gate.py pre    # PreToolUse
    python3 hooks/gate.py post   # PostToolUse

Python stdlib only.
"""

import json
import logging
import os
import sys
import time
from typing import Any

# Ensure core is importable
sys.path.insert(0, os.environ["CLAUDE_PLUGIN_ROOT"])

from core import (
    discover_and_merge,
    load_installed_plugins,
    load_state,
    save_state,
    setup_logging,
    utc_now_iso,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def read_stdin() -> tuple[str, dict[str, Any] | None]:
    """Read stdin once and return raw text with parsed dict.

    Returns:
        A (raw_text, parsed_dict) tuple.  parsed_dict is None if stdin
        is empty, not valid JSON, or not a JSON object.
    """
    try:
        raw = sys.stdin.read()
    except OSError:
        return "", None

    if not raw.strip():
        return raw, None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw, None

    if not isinstance(data, dict):
        return raw, None

    return raw, data


def extract_plugin_name(subagent_type: str) -> str | None:
    """Extract the plugin name from a subagent_type string.

    Claude sends plugin agent calls in different formats depending on
    how the plugin was loaded:

        "cute-puppies:cute-puppies"  -- marketplace install (colon-namespaced)
        "cute-puppies"               -- --plugin-dir or local filesystem

    Returns:
        The lowercased plugin name, or None if the string is empty.
    """
    if not subagent_type:
        return None
    return subagent_type.split(":", 1)[0].lower()


def match_plugin_by_subagent(
    tool_input: dict[str, Any],
    state: dict[str, Any],
) -> str | None:
    """Match a plugin by the subagent_type field in tool_input.

    Args:
        tool_input: The tool_input dict from the hook event.
        state: The current plugin-canary state dict.

    Returns:
        The full plugin key (e.g. "code-simplifier@claude-plugins-official")
        or None if no plugin matched.
    """
    subagent_type = tool_input.get("subagent_type", "")
    if not isinstance(subagent_type, str):
        return None

    candidates = [
        extract_plugin_name(subagent_type),  # "plugin:agent" -> "plugin"
        subagent_type.lower(),               # "plugin" bare   -> "plugin"
    ]

    plugins = state.get("plugins", {})
    if not isinstance(plugins, dict):
        return None

    for plugin_key in plugins:
        if not isinstance(plugin_key, str):
            continue
        key_name = plugin_key.partition("@")[0].lower()
        if key_name in candidates:
            return plugin_key

    return None


# ---------------------------------------------------------------------------
# Payload formatting
# ---------------------------------------------------------------------------


def build_stderr_payload(
    matched_plugins: set[str],
    state: dict[str, Any],
) -> str:
    """Build the exit-2 stderr payload instructing Claude to audit plugins.

    Args:
        matched_plugins: Set of plugin keys that need auditing.
        state: The current plugin-canary state dict.

    Returns:
        Multi-line string listing each plugin and its files for review.
    """
    plugins = state.get("plugins", {})
    sections: list[str] = []
    for key in sorted(matched_plugins):
        plugin_data = plugins.get(key, {})
        if not isinstance(plugin_data, dict):
            continue
        file_paths = plugin_data.get("files", [])
        if not isinstance(file_paths, list):
            continue
        files = "\n".join(f"  {fp}" for fp in file_paths if isinstance(fp, str))
        sections.append(f"Plugin: {key}\nFiles to review:\n{files}")

    body = "\n\n".join(sections)

    return f"""\
PLUGIN-CANARY: SECURITY REVIEW REQUIRED
==================================================

Before proceeding, launch a security review using the Task tool:
  subagent_type: "plugin-canary:plugin-canary"
  model: "opus"
  prompt: Include the plugin key and all file paths listed below.

{body}

After the review completes, present the agent's findings to the user,
then retry your original action regardless of the verdict. The user
will be prompted to allow or deny the tool call."""


# ---------------------------------------------------------------------------
# PreToolUse
# ---------------------------------------------------------------------------


def pretooluse_main() -> None:
    """PreToolUse gate -- block unaudited plugin agent calls.

    See module docstring for the full check order.  Includes mid-session
    re-discovery for plugins installed after SessionStart.

    Raises:
        SystemExit: Exit 2 to block the tool call when audit is needed.
    """
    start_time = time.time()
    logger.debug("=== GATE pre ===")

    # Read stdin once
    raw_stdin, hook_input = read_stdin()
    logger.debug("stdin: %s", raw_stdin[:2000])

    if hook_input is None:
        logger.debug("No valid stdin, allowing")
        return

    logger.debug("hook_input keys: %s", list(hook_input.keys()))

    tool_input = hook_input.get("tool_input")
    if not isinstance(tool_input, dict):
        logger.debug("No tool_input, allowing")
        return

    logger.debug(
        "tool=%s, tool_input keys=%s",
        hook_input.get("tool_name", "unknown"),
        list(tool_input.keys()),
    )

    # Don't intercept our own agent calls (prevents infinite loop)
    subagent_type = tool_input.get("subagent_type", "")
    if extract_plugin_name(subagent_type) == "plugin-canary":
        logger.debug("Own agent call (%s), allowing", subagent_type)
        return

    # Load state
    state = load_state()
    if not state.get("plugins"):
        logger.debug("No plugins in state, allowing")
        return

    # Match on subagent_type
    logger.debug("subagent_type: %s", subagent_type)
    matched_key = match_plugin_by_subagent(tool_input, state)

    # If a colon-namespaced agent isn't in state, the plugin may have been
    # installed mid-session (after SessionStart discovery).  Re-read the
    # registry and merge before giving up.
    if matched_key is None and isinstance(subagent_type, str) and subagent_type:
        logger.debug("Unknown plugin agent %s, re-discovering", subagent_type)
        registry = load_installed_plugins()
        if registry is not None:
            state = discover_and_merge(registry, state)
            try:
                save_state(state)
            except OSError:
                logger.exception("Failed to save re-discovered state")
            matched_key = match_plugin_by_subagent(tool_input, state)

    if matched_key is None:
        logger.debug("No plugin match, allowing")
        return

    logger.debug("Matched: %s", matched_key)

    # Check plugin state
    plugins = state.get("plugins", {})
    plugin_data = plugins.get(matched_key, {})
    if not isinstance(plugin_data, dict):
        logger.debug("Invalid plugin data for %s, allowing", matched_key)
        return

    decision = plugin_data.get("decision")

    if decision == "approved":
        logger.debug("%s: approved, allowing", matched_key)
        return

    if decision == "rejected":
        logger.debug("%s: rejected, blocking permanently", matched_key)
        result = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"PLUGIN-CANARY: {matched_key} was marked as dangerous "
                    "and will not run. To reverse this decision, run: "
                    "python3 scripts/manage.py revoke " + matched_key
                ),
            }
        }
        print(json.dumps(result))
        print(
            f"BLOCKED: {matched_key} was rejected as dangerous.",
            file=sys.stderr,
        )
        sys.exit(2)

    if plugin_data.get("audited", False):
        logger.debug("%s: already audited (retry), allowing", matched_key)
        return

    # Check that the plugin has files to review
    all_files = plugin_data.get("files", [])
    if not all_files:
        logger.debug("No files to review for %s, allowing", matched_key)
        return

    # Build payload
    stderr_payload = build_stderr_payload({matched_key}, state)

    # Mark audited in state BEFORE exit-2 so retry sees it.
    # If save fails, we still exit-2 (fail-closed: re-audit is safer than
    # letting an unreviewed plugin through).  Claude Code's hook runner
    # should surface the error, but the exact behavior for OSError in a
    # hook is undocumented as of 2026-02.  Log what we can.
    plugin_data["audited"] = True
    try:
        save_state(state)
    except OSError:
        logger.exception("Failed to persist audited flag; audit may repeat")

    elapsed = time.time() - start_time
    logger.debug(
        "Requesting Read of %d files (%d bytes payload) in %.3fs",
        len(all_files),
        len(stderr_payload),
        elapsed,
    )
    for f in all_files:
        logger.debug("  -> %s", f)

    # Triple output (the pattern that makes injection work):
    # 1. JSON on stdout -- structured hookSpecificOutput for Claude Code
    result = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": stderr_payload,
        }
    }
    print(json.dumps(result))

    # 2. Raw text on stderr -- Claude sees this as authoritative feedback
    print(stderr_payload, file=sys.stderr)

    # 3. Exit code 2 -- blocks the tool call
    logger.debug("=== GATE pre: BLOCKED ===")
    sys.exit(2)


# ---------------------------------------------------------------------------
# PostToolUse
# ---------------------------------------------------------------------------


def posttooluse_main() -> None:
    """PostToolUse clearance writer.

    Searches ALL string fields in tool_input for plugin names (not just
    subagent_type).  This lets the hook match when the audit Task
    completes -- the audit prompt contains the plugin key, so text
    matching picks it up and records the approval.

    Always exits 0.
    """
    logger.debug("=== GATE post ===")

    raw_stdin, hook_input = read_stdin()
    logger.debug("stdin: %s", raw_stdin[:2000])

    if hook_input is None:
        return

    tool_input = hook_input.get("tool_input")
    if not isinstance(tool_input, dict):
        return

    # Don't match on our own agent calls -- the audit Task's prompt
    # contains plugin names, so the broad text search below would
    # incorrectly auto-approve the plugin being audited.
    subagent_type = tool_input.get("subagent_type", "")
    if extract_plugin_name(subagent_type) == "plugin-canary":
        logger.debug("Own agent call (%s), skipping post", subagent_type)
        return

    state = load_state()
    plugins = state.get("plugins", {})
    if not plugins:
        return

    # Broad text search across all string fields in tool_input --
    # this is intentionally wider than PreToolUse's subagent_type
    # matching so that the audit task completion (whose prompt
    # contains the plugin key) triggers the approval.
    search_text = " ".join(
        v for v in tool_input.values() if isinstance(v, str)
    ).lower()
    if not search_text:
        return

    matched: set[str] = set()
    for plugin_key in plugins:
        if not isinstance(plugin_key, str):
            continue
        candidates = {plugin_key.lower()}
        if "@" in plugin_key:
            candidates.add(plugin_key.split("@", 1)[0].lower())
        for name in candidates:
            if name in search_text:
                matched.add(plugin_key)
                break

    if not matched:
        return

    logger.debug("Post matched: %s", matched)

    changed = False
    for key in matched:
        plugin_data = plugins.get(key)
        if not isinstance(plugin_data, dict):
            continue
        if plugin_data.get("decision") == "approved":
            logger.debug("%s: already approved", key)
            continue

        plugin_data["decision"] = "approved"
        plugin_data["decided_at"] = utc_now_iso()
        changed = True
        logger.debug("Approved: %s", key)

    if changed:
        save_state(state)

    logger.debug("=== GATE post done ===")


# ---------------------------------------------------------------------------
# CLI dispatch
# ---------------------------------------------------------------------------


def main() -> None:
    """Dispatch to pre or post handler based on CLI argument.

    Raises:
        SystemExit: On missing or unknown mode argument, or via
            pretooluse_main when blocking a tool call.
    """
    setup_logging()
    if len(sys.argv) < 2:
        print("Usage: gate.py <pre|post>", file=sys.stderr)
        sys.exit(1)

    mode = sys.argv[1]
    if mode == "pre":
        pretooluse_main()
    elif mode == "post":
        posttooluse_main()
    else:
        print(f"Unknown mode: {mode}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
