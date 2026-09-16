#!/usr/bin/env python3
"""Reverse the last sync with a revert commit. Never rewrites or force-pushes history."""
import argparse, json, os, subprocess, sys


def git(root, *args, check=False):
    return subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--commit", default="HEAD", help="the sync commit to reverse")
    ap.add_argument("--describe", action="store_true", help="report only, change nothing")
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()

    root = git(a.root, "rev-parse", "--show-toplevel").stdout.strip()
    if not root:
        print(json.dumps({"ok": False, "error": "not a git repository"}, indent=2))
        return 1
    rep = {"ok": True, "commit": None, "files": [], "warnings": []}

    sha = git(root, "rev-parse", a.commit).stdout.strip()
    if not sha:
        print(json.dumps({"ok": False, "error": "unknown commit %s" % a.commit}, indent=2))
        return 1
    rep["commit"] = sha[:12]
    rep["subject"] = git(root, "log", "-1", "--format=%s", sha).stdout.strip()
    rep["files"] = [x for x in git(root, "show", "--name-status", "--format=", sha
                                   ).stdout.strip().split("\n") if x]

    parents = git(root, "rev-list", "--parents", "-n", "1", sha).stdout.split()
    if len(parents) > 2:
        rep["warnings"].append("that commit is a merge; reverting it needs a chosen parent, "
                               "so ask the user which side to reverse")
        rep["ok"] = False
    descendants = git(root, "rev-list", "%s..HEAD" % sha).stdout.split()
    if descendants:
        rep["warnings"].append("%d commit(s) were made after it; only what reverses cleanly "
                               "can be undone" % len(descendants))
    if git(root, "status", "--porcelain").stdout.strip():
        rep["warnings"].append("the working tree has uncommitted changes; they are preserved "
                               "and left alone")

    if a.describe or not rep["ok"]:
        print(json.dumps(rep, indent=2))
        return 0 if rep["ok"] else 2

    r = git(root, "revert", "--no-edit", sha)
    if r.returncode != 0:
        conf = git(root, "diff", "--name-only", "--diff-filter=U").stdout.split()
        rep.update({"ok": False, "conflicts": conf,
                    "error": "the revert conflicts with later work",
                    "note": "nothing was discarded; resolve with the user, or run "
                            "`git revert --abort` to leave the repository as it was"})
        print(json.dumps(rep, indent=2))
        return 3
    rep["revert_commit"] = git(root, "rev-parse", "HEAD").stdout.strip()[:12]
    if not a.no_push:
        push = git(root, "push")               # never --force
        rep["pushed"] = push.returncode == 0
        if not rep["pushed"]:
            rep["status"] = "Reverted locally; upload incomplete"
            rep["push_error"] = (push.stderr or push.stdout).strip()[-500:]
    print(json.dumps(rep, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
