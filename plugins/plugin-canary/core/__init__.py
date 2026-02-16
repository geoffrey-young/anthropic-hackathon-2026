"""Shared state layer for plugin-canary.

Single persistent state file at ${CLAUDE_PLUGIN_ROOT}/resources/state.json.
Provides state I/O, content hashing, path helpers, and plugin discovery.

Python stdlib only.
"""

import hashlib
import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Skip list
# ---------------------------------------------------------------------------

# Plugins matching these patterns are never audited.
# Each pattern is matched as an exact key or as the name portion before @.
# "plugin-canary" matches "plugin-canary" and "plugin-canary@local",
# but NOT "plugin-canary-backdoor".
SKIP_PATTERNS = [
    "plugin-canary",  # never audit ourselves
    # "@claude-plugins-official",  # trust Anthropic official plugins
]


def should_skip(plugin_key: str) -> bool:
    """Check if a plugin matches the skip list.

    Splits plugin_key on ``@`` and checks the name and registry parts
    independently against each pattern, requiring exact matches.

    Args:
        plugin_key: Full plugin key, e.g. "plugin-canary@local".

    Returns:
        True if the plugin should be excluded from auditing.
    """
    key_lower = plugin_key.lower()
    name, _, registry = key_lower.partition("@")
    for pattern in SKIP_PATTERNS:
        p = pattern.lower()
        if p.startswith("@"):
            # Registry pattern: match the @registry portion exactly
            if registry == p[1:]:
                return True
        # Name pattern: match the name portion (or full key) exactly
        elif p in {name, key_lower}:
            return True
    return False


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def get_state_path() -> str:
    """Return the absolute path to the persistent state file.

    Returns:
        Path to resources/state.json under CLAUDE_PLUGIN_ROOT.

    Raises:
        RuntimeError: If CLAUDE_PLUGIN_ROOT is not set.
    """
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if not root:
        raise RuntimeError("CLAUDE_PLUGIN_ROOT environment variable is not set")
    return os.path.join(root, "resources", "state.json")


# ---------------------------------------------------------------------------
# State I/O
# ---------------------------------------------------------------------------


def load_state() -> dict[str, Any]:
    """Load state.json, returning empty structure if missing or invalid.

    Returns:
        The state dict with at least ``version`` and ``plugins`` keys.
    """
    path = get_state_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "plugins": {}}
    if not isinstance(data, dict):
        return {"version": 1, "plugins": {}}
    return data


def save_state(state: dict[str, Any]) -> None:
    """Write state.json atomically (write tmp, then replace).

    Args:
        state: The full state dict to persist.

    Raises:
        OSError: On write failure.  Callers decide how to handle it.
    """
    path = get_state_path()
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp_path, path)


# ---------------------------------------------------------------------------
# Content hashing
# ---------------------------------------------------------------------------


def compute_content_hash(dir_path: str) -> str:
    """Hash file contents for change detection.

    Reads actual file bytes so modifications are always detected,
    regardless of mtime preservation (git checkout, os.replace, etc).

    Args:
        dir_path: Absolute path to the plugin directory.

    Returns:
        ``"sha256:<hex>"`` or ``"sha256:empty"`` if the directory is
        missing or contains no files.
    """
    h = hashlib.sha256()
    found = False
    if os.path.isdir(dir_path):
        for root, dirs, files in os.walk(dir_path):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for name in sorted(files):
                file_path = os.path.join(root, name)
                try:
                    rel = os.path.relpath(file_path, dir_path)
                    h.update(rel.encode())
                    with open(file_path, "rb") as f:
                        for chunk in iter(lambda: f.read(8192), b""):
                            h.update(chunk)
                    found = True
                except OSError:
                    continue
    if not found:
        return "sha256:empty"
    return "sha256:" + h.hexdigest()


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------


def collect_plugin_files(dir_path: str) -> list[str]:
    """Walk a plugin directory and return a sorted list of all file paths.

    Skips ``__pycache__`` directories.

    Args:
        dir_path: Absolute path to the plugin directory.

    Returns:
        Sorted list of absolute file paths, empty if dir_path is missing.
    """
    result: list[str] = []
    if not os.path.isdir(dir_path):
        return result
    for root, dirs, files in os.walk(dir_path):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        result.extend(os.path.join(root, name) for name in sorted(files))
    return result


# ---------------------------------------------------------------------------
# Timestamp helper
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string.

    Returns:
        Timestamp in ``YYYY-MM-DDTHH:MM:SSZ`` format.
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_LOG_DIR = os.path.join(tempfile.gettempdir(), "claude")
_LOG_FILE = os.path.join(_LOG_DIR, "plugin-canary.log")


def setup_logging() -> None:
    """Configure file logging for plugin-canary hooks.

    Writes to ``$TMPDIR/claude/plugin-canary.log`` (respects sandbox
    TMPDIR).  Safe to call multiple times; only the first call has
    effect.
    """
    os.makedirs(_LOG_DIR, exist_ok=True)
    root = logging.getLogger()
    if not any(
        isinstance(h, logging.FileHandler) and h.baseFilename == _LOG_FILE
        for h in root.handlers
    ):
        handler = logging.FileHandler(_LOG_FILE)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
        )
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)


# ---------------------------------------------------------------------------
# Plugin discovery
# ---------------------------------------------------------------------------

INSTALLED_PLUGINS_PATH = os.path.join(
    os.path.expanduser("~"), ".claude", "plugins", "installed_plugins.json"
)


def load_installed_plugins() -> dict[str, Any] | None:
    """Load the Claude Code installed plugins registry.

    Returns:
        The parsed JSON dict, or None if the file is missing, unreadable,
        or not a JSON object.
    """
    try:
        with open(INSTALLED_PLUGINS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return data
    except (OSError, json.JSONDecodeError) as e:
        logger.debug("Could not load installed_plugins.json: %s", e)
        return None


def discover_and_merge(
    registry: dict[str, Any], existing_state: dict[str, Any]
) -> dict[str, Any]:
    """Smart merge: compare installed plugins against existing state.

    New plugins get ``audited=False, decision=None``.  Unchanged plugins
    (same content hash) keep their audit state.  Changed plugins are
    reset.  Uninstalled plugins are dropped.

    Args:
        registry: Parsed contents of installed_plugins.json.
        existing_state: The current plugin-canary state dict.

    Returns:
        The updated state dict (mutates and returns existing_state).
    """
    plugins_data = registry.get("plugins", {})
    if not isinstance(plugins_data, dict):
        return existing_state

    existing_plugins = existing_state.get("plugins", {})
    if not isinstance(existing_plugins, dict):
        existing_plugins = {}

    updated_plugins: dict[str, Any] = {}

    for plugin_key, entries in plugins_data.items():
        if not isinstance(plugin_key, str):
            continue
        if not isinstance(entries, list) or not entries:
            continue

        # Skip list check
        if should_skip(plugin_key):
            logger.debug("Skipping (skip list): %s", plugin_key)
            continue

        entry = entries[0]
        if not isinstance(entry, dict):
            continue

        install_path = entry.get("installPath")
        if not isinstance(install_path, str):
            continue

        if not os.path.isdir(install_path):
            logger.debug("Plugin dir does not exist: %s", install_path)
            continue

        # Collect files and hash
        file_paths = collect_plugin_files(install_path)
        if not file_paths:
            logger.debug("No files found in %s", plugin_key)
            continue

        content_hash = compute_content_hash(install_path)

        # Smart merge against existing state
        existing = existing_plugins.get(plugin_key)
        if isinstance(existing, dict) and existing.get("content_hash") == content_hash:
            # Hash matches -- keep existing state (audit + decision intact)
            updated_plugins[plugin_key] = existing
            # Update files list in case walk order changed (harmless)
            updated_plugins[plugin_key]["files"] = file_paths
            logger.debug("Unchanged %s (hash %s)", plugin_key, content_hash[:16])
        else:
            # New or changed -- fresh entry
            reason = "new" if existing is None else "changed"
            updated_plugins[plugin_key] = {
                "install_path": install_path,
                "files": file_paths,
                "content_hash": content_hash,
                "audited": False,
                "decision": None,
                "decided_at": None,
            }
            logger.debug(
                "Discovered %s (%s): %d files, hash %s",
                plugin_key,
                reason,
                len(file_paths),
                content_hash[:16],
            )

    # Plugins in old state but not in registry are dropped (uninstalled)
    dropped = set(existing_plugins.keys()) - set(updated_plugins.keys())
    for key in dropped:
        logger.debug("Dropped uninstalled plugin: %s", key)

    existing_state["version"] = 1
    existing_state["plugins"] = updated_plugins
    return existing_state
