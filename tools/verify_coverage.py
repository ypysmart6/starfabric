#!/usr/bin/env python3
"""Fail CI when the roadmap coverage manifest is incomplete or overclaims evidence."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "coverage-manifest.json"


def main() -> None:
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise SystemExit("coverage manifest schema_version must be 1")
    entries = document.get("entries")
    if not isinstance(entries, list) or not entries:
        raise SystemExit("coverage manifest entries must be a non-empty list")

    identifiers: set[str] = set()
    phases: set[int] = set()
    counts = {"A": 0, "B": 0, "C": 0}
    for entry in entries:
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in identifiers:
            raise SystemExit(f"invalid or duplicate coverage id: {identifier!r}")
        identifiers.add(identifier)
        phase = entry.get("phase")
        if not isinstance(phase, int) or phase not in range(7):
            raise SystemExit(f"{identifier}: phase must be 0..6")
        phases.add(phase)
        level = entry.get("level")
        if level not in counts:
            raise SystemExit(f"{identifier}: level must be A, B or C")
        counts[level] += 1
        technologies = entry.get("technologies")
        if not isinstance(technologies, list) or not technologies or any(not isinstance(item, str) or not item for item in technologies):
            raise SystemExit(f"{identifier}: technologies must be non-empty strings")
        evidence = entry.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise SystemExit(f"{identifier}: evidence is required")
        for relative in evidence:
            path = ROOT / relative
            if not path.exists() or not path.is_file():
                raise SystemExit(f"{identifier}: missing evidence {relative}")
        if level in {"B", "C"} and not entry.get("boundary"):
            raise SystemExit(f"{identifier}: external/hardware coverage requires a boundary")

    if phases != set(range(7)):
        raise SystemExit(f"manifest must cover every phase 0..6; found {sorted(phases)}")

    dependency_files = [ROOT / "go.mod", ROOT / "onboard" / "Cargo.toml", ROOT / "tools" / "requirements-orbit.txt"]
    dependency_text = "\n".join(path.read_text(encoding="utf-8").lower() for path in dependency_files)
    for dependency in document.get("forbidden_dependencies", []):
        if re.search(rf"(^|[/\s_-]){re.escape(dependency.lower())}([/\s_.-]|$)", dependency_text, re.MULTILINE):
            raise SystemExit(f"forbidden dependency present: {dependency}")

    print(f"PASS: {len(entries)} roadmap capabilities; A={counts['A']} B={counts['B']} C={counts['C']}; phases=0..6")


if __name__ == "__main__":
    main()
