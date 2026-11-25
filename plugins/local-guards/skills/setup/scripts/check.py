#!/usr/bin/env python3
import json
import os
from pathlib import Path
import re
import shutil
import sys

try:
    import tomllib
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    try:
        import _tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None


def hook_commands(config):
    for event, groups in config.get("hooks", {}).items():
        if not isinstance(groups, list):
            continue
        for group_index, group in enumerate(groups):
            for handler_index, handler in enumerate(group.get("hooks", [])):
                yield event, group_index, handler_index, handler.get("command", "")


def main():
    missing = [command for command in ("bash", "git", "jq", "python3") if shutil.which(command) is None]
    if missing:
        print("Missing required commands: " + ", ".join(missing))
        return 1
    home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    sources = {}
    states = {}
    duplicates = []
    for path in (home / "config.toml", home / "hooks.json"):
        if not path.exists():
            continue
        if path.suffix == ".toml" and tomllib is None:
            print("config.toml inspection skipped: this python3 lacks tomllib (3.11+)")
            continue
        try:
            content = path.read_text()
            config = tomllib.loads(content) if path.suffix == ".toml" else json.loads(content)
            sources[path] = config
            if path.suffix == ".toml":
                states = config.get("hooks", {}).get("state", {})
                if not isinstance(states, dict):
                    raise ValueError("hooks.state must be a table")
        except (OSError, ValueError, AttributeError, TypeError) as error:
            print(f"Cannot inspect {path}: {error}")
            return 1
    for path, config in sources.items():
        try:
            for event, group_index, handler_index, command in hook_commands(config):
                event_id = re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()
                state = states.get(f"{path}:{event_id}:{group_index}:{handler_index}", {})
                if isinstance(state, dict) and state.get("enabled") is False:
                    continue
                if "PLUGIN_ROOT" in command:
                    continue
                for name in ("deny-git-push.sh", "backup-ref.sh", "edit-checks.py"):
                    if name in command:
                        duplicates.append(f"{path}: {event}: {command}")
                        break
        except (OSError, ValueError, AttributeError, TypeError) as error:
            print(f"Cannot inspect {path}: {error}")
            return 1
    if duplicates:
        print("Matching local hooks would run alongside this plugin:")
        print("\n".join(duplicates))
        print("Disable the matching local hook entries before enabling this plugin; preserve unrelated hooks.")
        print("This check changed no configuration. Review the entries with the user before deactivating them.")
        return 2
    print("Dependencies available; no matching local hooks found. Plugin activation and hook trust remain separate steps.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
