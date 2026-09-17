#!/usr/bin/env python3
"""Run real Batfish parse, reference, loop, and reachability questions."""
import argparse
import json
from pathlib import Path

from pybatfish.client.session import Session


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9996)
    parser.add_argument("--snapshot", type=Path, default=Path(__file__).resolve().parent / "snapshot")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[2] / "reports/batfish.json")
    args = parser.parse_args()

    session = Session(host=args.host, port=args.port)
    session.set_network("starfabric")
    session.init_snapshot(str(args.snapshot), name="starfabric-frr", overwrite=True)

    parse = session.q.fileParseStatus().answer().frame()
    statuses = parse["Status"].astype(str)
    failed = parse[~statuses.str.endswith("PASSED")]
    loops = session.q.detectLoops().answer().frame()
    undefined = session.q.undefinedReferences().answer().frame()
    reachability = session.q.reachability(
        pathConstraints={"startLocation": "@enter(r1[Ethernet1])"},
        headers={"dstIps": "198.51.100.2", "ipProtocols": ["UDP"]},
        actions="SUCCESS",
    ).answer().frame()

    checks = {
        "all_configurations_parsed": failed.empty and len(parse.index) == 4,
        "no_forwarding_loops": loops.empty,
        "no_undefined_references": undefined.empty,
        "modeled_udp_reachability": not reachability.empty,
    }
    report = {
        "success": all(checks.values()),
        "evidence_level": "live Batfish service snapshot and question execution",
        "batfish_version": session.get_component_versions().get("Batfish"),
        "checks": checks,
        "parsed_nodes": len(parse.index),
        "loop_rows": len(loops.index),
        "undefined_reference_rows": len(undefined.index),
        "successful_flow_rows": len(reachability.index),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not report["success"]:
        if not failed.empty:
            print(f"configuration parse failures:\n{failed}")
        if not loops.empty:
            print(f"forwarding loops:\n{loops}")
        if not undefined.empty:
            print(f"undefined references:\n{undefined}")
        raise SystemExit("Batfish closed-loop assertions failed")


if __name__ == "__main__":
    main()
