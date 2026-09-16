#!/usr/bin/env python3
"""Break each guarantee on purpose and confirm a test catches it.

A passing suite proves nothing if the tests cannot fail. Each mutation below removes one
real protection; the named test must go red, and the source is always restored.
"""
import os, re, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")

MUTATIONS = [
    ("archive consent check removed", "apply.py",
     'if not mv.get("consent"):', 'if False:',
     "guardrail", "GUARD-06"),
    ("path containment disabled", "apply.py",
     'return (a == root or a.startswith(root + os.sep)) and ".git" not in a.split(os.sep)',
     'return True',
     "hostile", "HOSTILE-01"),
    ("output overwrite guard removed", "apply.py",
     'if known.get("output") != dest_rel or known.get("output_hash") != sha(dest):',
     'if False:',
     "hostile", "HOSTILE-04"),
    ("migration coupling removed", "apply.py",
     'if out.get("requires_archive_consent") and out["src"] not in consented:',
     'if False:',
     "extra", "DOC-02"),
    ("detached HEAD allowed", "apply.py",
     'if branch == "HEAD":', 'if False:',
     "hostile", "HOSTILE-10"),
    ("approval scope widened to everything", "apply.py",
     'approved = [p for p in plan.get("approved_paths", []) if inside(root, p)]',
     'approved = [p for p in os.listdir(root) if p != ".git"]',
     "flow", "SELECT-01"),
    ("conversion memory always says unchanged", "scan.py",
     'if src_same and out_same:', 'if True:',
     "flow", "IMAGE-04"),
    ("csv limit raised", "policy.py",
     'MB = 1_000_000', 'MB = 10_000_000',
     "policy", "FILE-01"),
    ("experimental formats allowed", "policy.py",
     'return "experimental", "exclude", "raw experimental format", None',
     'return "experimental", "upload", None, None',
     "policy", "FILE-02"),
    ("cleanup instructions accepted", "apply.py",
     'for key in ("delete", "deletions", "cleanup", "remove"):',
     'for key in ():',
     "guardrail", "GUARD-08"),
]


def run_module(module):
    p = subprocess.run([sys.executable, os.path.join(HERE, "run_tests.py"),
                        "--only=" + module], capture_output=True, text=True)
    return p.stdout


def main():
    backups = {}
    for _, f, _, _, _, _ in MUTATIONS:
        path = os.path.join(SCRIPTS, f)
        if path not in backups:
            backups[path] = open(path).read()
    results = []
    try:
        for label, fname, old, new, module, expect in MUTATIONS:
            path = os.path.join(SCRIPTS, fname)
            src = backups[path]
            if src.count(old) != 1:
                results.append((label, "SKIP", "anchor not unique in %s" % fname))
                print("  SKIP  %-46s (anchor moved: %s)" % (label, fname))
                continue
            open(path, "w").write(src.replace(old, new))
            out = run_module(module)
            open(path, "w").write(src)                     # restore immediately
            caught = [l for l in out.splitlines()
                      if re.match(r"\s+(FAIL|ERROR)\s", l) and expect in l]
            if caught:
                results.append((label, "CAUGHT", expect))
                print("  CAUGHT %-45s by %s" % (label, expect))
            else:
                any_red = [l for l in out.splitlines() if re.match(r"\s+(FAIL|ERROR)\s", l)]
                results.append((label, "MISSED", "expected %s; red: %s"
                                % (expect, [l.split()[1] for l in any_red][:4])))
                print("  MISSED %-45s expected %s to fail" % (label, expect))
    finally:
        for path, src in backups.items():
            open(path, "w").write(src)
        print("\nsources restored")
    missed = [r for r in results if r[1] == "MISSED"]
    print("%d/%d mutations caught by the named test"
          % (len([r for r in results if r[1] == "CAUGHT"]), len(results)))
    for label, st, msg in results:
        if st != "CAUGHT":
            print("  %-7s %s: %s" % (st, label, msg))
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
