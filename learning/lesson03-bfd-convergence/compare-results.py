#!/usr/bin/env python3

import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
BASELINE = HERE.parent / "lesson02-isis-control-plane" / "reports" / "latest.json"
BFD = HERE / "reports" / "latest.json"


def load(path: Path) -> dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(f"missing {path}; run 'make ab' or generate the missing measurement")
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    try:
        baseline = load(BASELINE)
        bfd = load(BFD)
        baseline_fib = float(baseline["failure"]["fib_switch_ms"])
        bfd_fib = float(bfd["failure"]["fib_switch_ms"])
        baseline_lost = int(baseline["ping"]["lost"])
        bfd_lost = int(bfd["ping"]["lost"])
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    speedup = baseline_fib / bfd_fib
    saved = baseline_lost - bfd_lost
    print("A/B RESULT: same topology, traffic and bidirectional silent-blackhole fault")
    print(f"  IS-IS only : FIB switch {baseline_fib:.1f} ms, ping loss {baseline_lost}")
    print(f"  IS-IS+BFD  : FIB switch {bfd_fib:.1f} ms, ping loss {bfd_lost}")
    print(f"  Improvement: {speedup:.2f}x faster, {saved} fewer lost packets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
