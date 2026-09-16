#!/usr/bin/env python3
"""Run the whole synctogit suite against throwaway mock repositories."""
import os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import Suite
import test_policy, test_guardrail, test_flow, test_setup, test_extra, test_hostile, test_e2e

MODULES = [("policy", test_policy), ("guardrail", test_guardrail),
           ("flow", test_flow), ("setup", test_setup), ("extra", test_extra), ("hostile", test_hostile), ("e2e", test_e2e)]


def main():
    keep = "--keep" in sys.argv
    only = None
    for a in sys.argv[1:]:
        if a.startswith("--only="):
            only = a.split("=", 1)[1].lower()
    s = Suite()
    print("workspace: %s\n" % s.base)
    t0 = time.time()
    for name, mod in MODULES:
        if only and only not in name:
            continue
        print("%s:" % name)
        s.run(mod.TESTS)
        print("")
    total = len(s.results)
    bad = [r for r in s.results if r[1] != "PASS"]
    print("%d/%d passed in %.1fs" % (total - len(bad), total, time.time() - t0))
    for tid, st, msg in bad:
        print("  %-6s %s\n         %s" % (st, tid, msg.replace("\n", "\n         ")))
    if keep:
        print("\nkept workspace: %s" % s.base)
    else:
        s.cleanup()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
