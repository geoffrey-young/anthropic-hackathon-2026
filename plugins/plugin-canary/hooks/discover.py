#!/usr/bin/env python3
"""SessionStart hook -- discovers installed plugins and updates state.

Delegates to ``core.discover_and_merge`` for the heavy lifting.  The
merge semantics are:

  - New plugin: add with audited=false, decision=null.
  - Existing plugin, same hash: keep everything (audit + decision).
  - Existing plugin, hash changed: reset audited=false, clear decision.
  - Plugin no longer installed: remove from state.

Python stdlib only.
"""

import json
import logging
import os
import sys
import time

# Ensure core is importable
sys.path.insert(0, os.environ["CLAUDE_PLUGIN_ROOT"])

from core import (
    discover_and_merge,
    load_installed_plugins,
    load_state,
    save_state,
    setup_logging,
)

logger = logging.getLogger(__name__)


def main() -> None:
    """Run the discovery hook.

    Reads the installed plugins registry, merges with existing state,
    and prints a summary to stdout (visible to Claude on SessionStart).

    Raises:
        SystemExit: Always exits 0 after discovery completes.
    """
    setup_logging()
    start_time = time.time()
    logger.debug("=== Discovery started ===")

    # Read stdin (SessionStart provides session metadata)
    try:
        stdin_text = sys.stdin.read()
        if stdin_text.strip():
            hook_input = json.loads(stdin_text)
            logger.debug("stdin: %s", json.dumps(hook_input, indent=2))
    except (json.JSONDecodeError, OSError) as e:
        logger.debug("Could not read stdin: %s", e)

    # Load installed plugins registry
    registry = load_installed_plugins()
    if registry is None:
        logger.debug("No installed_plugins.json, exiting")
        print("plugin-canary: no plugins installed")
        sys.exit(0)

    # Smart merge with existing state
    existing_state = load_state()
    updated_state = discover_and_merge(registry, existing_state)
    save_state(updated_state)

    # Summary
    plugins = updated_state.get("plugins", {})
    if not plugins:
        print("plugin-canary: no plugins to audit")
    else:
        names = ", ".join(sorted(plugins.keys()))
        print(f"plugin-canary: {len(plugins)} plugins ({names})")

    elapsed = time.time() - start_time
    logger.debug("Discovery completed in %.3f seconds", elapsed)
    logger.debug("=== Discovery finished ===")


if __name__ == "__main__":
    main()
