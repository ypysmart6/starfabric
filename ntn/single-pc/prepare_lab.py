#!/usr/bin/env python3
"""Remove only stopped containers owned by this acceptance project."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

PROJECT = "starfabric-5g"


def removable_id(container: dict[str, Any]) -> str:
    owner = (container.get("Config", {}).get("Labels") or {}).get("com.docker.compose.project")
    state = container.get("State", {})
    if owner != PROJECT:
        raise ValueError(f"container {container.get('Name')} belongs to another project")
    if state.get("Running") or state.get("Paused") or state.get("Status") not in {"created", "exited", "dead"}:
        raise ValueError(f"container {container.get('Name')} is active; stop it explicitly before running acceptance")
    identifier = container.get("Id")
    if not isinstance(identifier, str) or len(identifier) != 64 or any(c not in "0123456789abcdef" for c in identifier):
        raise ValueError("invalid container ID")
    return identifier


def main(names: list[str]) -> None:
    # Inspect all names before removing anything, so a foreign/active collision
    # leaves the entire pre-existing lab untouched. Remove by ID to avoid races.
    stale = []
    for name in names:
        result = subprocess.run(["docker", "inspect", name], text=True, capture_output=True, check=False)
        if result.returncode:
            if "no such object" in result.stderr.lower() or "no such container" in result.stderr.lower():
                continue
            raise RuntimeError(f"cannot inspect container {name}: {result.stderr.strip()}")
        stale.append((name, removable_id(json.loads(result.stdout)[0])))
    for name, identifier in stale:
        # No --force and no --volumes: reject concurrent starts, retain data.
        subprocess.run(["docker", "rm", identifier], check=True, stdout=subprocess.DEVNULL)
        print(f"Removed stopped {PROJECT} container {name}; volumes retained")


if __name__ == "__main__":
    main(sys.argv[1:])
