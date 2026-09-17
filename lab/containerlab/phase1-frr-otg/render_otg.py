#!/usr/bin/env python3
import json
import sys
from pathlib import Path

root = Path(__file__).parent / "clab-sf-phase1" / "topology-data.json"
data = json.loads(root.read_text(encoding="utf-8"))
links = data["links"]


def endpoints(link):
    """Accept both the pre-0.79 and current containerlab topology-data schema."""
    value = link.get("endpoints", link)
    if not isinstance(value, dict) or "a" not in value or "z" not in value:
        raise SystemExit(f"unsupported containerlab link schema: {link!r}")
    return value["a"], value["z"]


def link_between(first, second):
    for link in links:
        a, z = endpoints(link)
        if {a.get("node"), z.get("node")} == {first, second}:
            return a, z
    raise SystemExit(f"containerlab topology has no {first}<->{second} link")


def endpoint_for(pair, node):
    for endpoint in pair:
        if endpoint.get("node") == node:
            return endpoint
    raise SystemExit(f"link has no endpoint for {node}")


if len(sys.argv) == 2 and sys.argv[1] == "--host-interface":
    pair = link_between("r1", "r2")
    endpoint = endpoint_for(pair, "r1")
    print(endpoint.get("host-interface", endpoint.get("host_ifname", "")))
    raise SystemExit
source, output = map(Path, sys.argv[1:3])
text = source.read_text(encoding="utf-8")
left = link_between("otg-a", "r1")
right = link_between("r4", "otg-b")
replacements = {
    "00:00:00:00:11:aa": endpoint_for(left, "otg-a")["mac"],
    "00:00:00:00:11:bb": endpoint_for(left, "r1")["mac"],
    "00:00:00:00:22:aa": endpoint_for(right, "otg-b")["mac"],
    "00:00:00:00:22:bb": endpoint_for(right, "r4")["mac"],
}
for old, new in replacements.items():
    text = text.replace(old, new)
output.write_text(text, encoding="utf-8")
