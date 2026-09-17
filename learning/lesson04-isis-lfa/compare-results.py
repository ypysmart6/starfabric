#!/usr/bin/env python3

import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
BASELINE = HERE.parent / "lesson03-bfd-convergence" / "reports" / "latest.json"
LFA = HERE / "reports" / "latest.json"


def load(path: Path) -> dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(
            f"missing {path}; run 'make ab' or generate the missing measurement"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    try:
        baseline = load(BASELINE)
        lfa = load(LFA)
        if baseline.get("fault") != lfa.get("fault") or baseline.get("traffic") != lfa.get("traffic"):
            raise ValueError("reports do not use the same fault and traffic conditions")
        baseline_fib = float(baseline["failure"]["fib_switch_ms"])
        lfa_fib = float(lfa["failure"]["fib_switch_ms"])
        baseline_lost = int(baseline["ping"]["lost"])
        lfa_lost = int(lfa["ping"]["lost"])
        preinstalled = bool(lfa["protection"]["backup_preinstalled_before_fault"])
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    delta = lfa_fib - baseline_fib
    print("A/B RESULT: same topology, BFD timers, traffic and silent-blackhole fault")
    print(f"  IS-IS+BFD      : FIB switch {baseline_fib:.1f} ms, ping loss {baseline_lost}")
    print(f"  IS-IS+BFD+LFA  : FIB switch {lfa_fib:.1f} ms, ping loss {lfa_lost}")
    print(f"  Observed delta : {delta:+.1f} ms, {lfa_lost - baseline_lost:+d} lost packets")
    print(f"  LFA property   : backup preinstalled before fault = {str(preinstalled).lower()}")
    print("INTERPRETATION: BFD detection dominates this tiny lab; use 'make prove-local'")
    print("to separate local repair from a deliberately delayed full SPF calculation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
