#!/usr/bin/env python3
"""State management CLI for plugin-canary.

Usage:
    python3 manage.py list
    python3 manage.py status <plugin-name>
    python3 manage.py approve <plugin-key>
    python3 manage.py reject <plugin-key>
    python3 manage.py revoke <plugin-key>
"""

import json
import os
import sys

# When run as CLI, CLAUDE_PLUGIN_ROOT might not be set.
# Derive from script location: scripts/manage.py -> plugin root.
if "CLAUDE_PLUGIN_ROOT" not in os.environ:
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    os.environ["CLAUDE_PLUGIN_ROOT"] = os.path.dirname(_script_dir)

sys.path.insert(0, os.environ["CLAUDE_PLUGIN_ROOT"])

from core import load_state, save_state, utc_now_iso


def cmd_list() -> None:
    """Print a one-line summary of every plugin in state."""
    state = load_state()
    plugins = state.get("plugins", {})
    if not plugins:
        print("No plugins in state.")
        return
    for key in sorted(plugins):
        data = plugins[key]
        if not isinstance(data, dict):
            continue
        decision = data.get("decision", "-")
        audited = data.get("audited", False)
        content_hash = str(data.get("content_hash", ""))[:16]
        n_files = len(data.get("files", []))
        print(
            f"  {key}: decision={decision}, audited={audited},"
            f" files={n_files}, hash={content_hash}..."
        )


def cmd_status(plugin_name: str) -> None:
    """Print full JSON state for a plugin matched by name.

    Matches on the exact full key or the name portion before ``@``.

    Args:
        plugin_name: Plugin name or full key to look up.
    """
    state = load_state()
    plugins = state.get("plugins", {})
    name_lower = plugin_name.lower()
    matches = {
        k: v
        for k, v in plugins.items()
        if isinstance(v, dict)
        and (k.lower() == name_lower or k.partition("@")[0].lower() == name_lower)
    }
    if not matches:
        print(f"Plugin '{plugin_name}' not found in state.")
        return
    for key, data in sorted(matches.items()):
        print(json.dumps({key: data}, indent=2))


def cmd_approve(plugin_key: str) -> None:
    """Manually approve a plugin, marking it as audited.

    Args:
        plugin_key: Exact plugin key (e.g.
            "code-simplifier@claude-plugins-official").

    Raises:
        SystemExit: If the state file cannot be written.
    """
    state = load_state()
    plugins = state.get("plugins", {})
    if plugin_key not in plugins:
        print(f"Plugin '{plugin_key}' not found in state.")
        return
    plugin_data = plugins[plugin_key]
    if not isinstance(plugin_data, dict):
        print(f"Invalid state for '{plugin_key}'.")
        return
    plugin_data["decision"] = "approved"
    plugin_data["decided_at"] = utc_now_iso()
    plugin_data["audited"] = True
    try:
        save_state(state)
    except OSError as e:
        print(f"Error: could not write state: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Approved: {plugin_key}")


def cmd_reject(plugin_key: str) -> None:
    """Mark a plugin as rejected (dangerous).  Blocks future use.

    Args:
        plugin_key: Exact plugin key (e.g.
            "cute-puppies@dog-park").

    Raises:
        SystemExit: If the state file cannot be written.
    """
    state = load_state()
    plugins = state.get("plugins", {})
    if plugin_key not in plugins:
        print(f"Plugin '{plugin_key}' not found in state.")
        return
    plugin_data = plugins[plugin_key]
    if not isinstance(plugin_data, dict):
        print(f"Invalid state for '{plugin_key}'.")
        return
    plugin_data["decision"] = "rejected"
    plugin_data["decided_at"] = utc_now_iso()
    plugin_data["audited"] = True
    try:
        save_state(state)
    except OSError as e:
        print(f"Error: could not write state: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Rejected: {plugin_key} (will be blocked on future use)")


def cmd_revoke(plugin_key: str) -> None:
    """Revoke approval and force re-audit on next use.

    Args:
        plugin_key: Exact plugin key (e.g.
            "code-simplifier@claude-plugins-official").

    Raises:
        SystemExit: If the state file cannot be written.
    """
    state = load_state()
    plugins = state.get("plugins", {})
    if plugin_key not in plugins:
        print(f"Plugin '{plugin_key}' not found in state.")
        return
    plugin_data = plugins[plugin_key]
    if not isinstance(plugin_data, dict):
        print(f"Invalid state for '{plugin_key}'.")
        return
    plugin_data["decision"] = None
    plugin_data["decided_at"] = None
    plugin_data["audited"] = False
    try:
        save_state(state)
    except OSError as e:
        print(f"Error: could not write state: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Revoked: {plugin_key} (will re-audit on next use)")


def main() -> None:
    """Parse CLI arguments and dispatch to the appropriate subcommand.

    Raises:
        SystemExit: On missing or unknown subcommand.
    """
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    if command == "list":
        cmd_list()
    elif command == "status":
        if len(sys.argv) < 3:
            print("Usage: manage.py status <plugin-name>")
            sys.exit(1)
        cmd_status(sys.argv[2])
    elif command == "approve":
        if len(sys.argv) < 3:
            print("Usage: manage.py approve <plugin-key>")
            sys.exit(1)
        cmd_approve(sys.argv[2])
    elif command == "reject":
        if len(sys.argv) < 3:
            print("Usage: manage.py reject <plugin-key>")
            sys.exit(1)
        cmd_reject(sys.argv[2])
    elif command == "revoke":
        if len(sys.argv) < 3:
            print("Usage: manage.py revoke <plugin-key>")
            sys.exit(1)
        cmd_revoke(sys.argv[2])
    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
