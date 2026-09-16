#!/usr/bin/env python3
"""Scan a project and decide, per file, what the policy allows. Changes nothing.

Emits JSON on stdout. Every decision comes from policy.py, never from recollection.
"""
import argparse, hashlib, json, os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import policy
from policy import MB

MANIFEST = ".synctogit.json"
# A dataless placeholder reports a size but has no blocks. Plain sparse files do too, so only
# trust this inside a cloud-sync tree, or when the name is an iCloud stub.
CLOUD_MARKERS = ("/library/mobile documents", "/dropbox", "/onedrive", "/google drive")


def git(root, *args, check=True):
    p = subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), p.stderr.strip()))
    return p.stdout


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def probe(abspath, root):
    """Return (size, symlink_outside, unreadable, placeholder)."""
    outside = False
    if os.path.islink(abspath):
        target = os.path.realpath(abspath)
        if not (target == root or target.startswith(root + os.sep)):
            outside = True
    try:
        st = os.stat(abspath)
    except OSError:
        return 0, outside, True, False
    size = st.st_size
    low = abspath.lower()
    in_cloud = any(m in low for m in CLOUD_MARKERS)
    placeholder = low.endswith(".icloud") or \
        (in_cloud and getattr(st, "st_blocks", 1) == 0 and size > 0)
    if placeholder:
        return size, outside, False, True
    unreadable = False
    if os.path.isfile(abspath):
        try:
            with open(abspath, "rb") as f:
                f.read(1)
        except OSError:
            unreadable = True
    return size, outside, unreadable, placeholder


def looks_binary(abspath):
    try:
        with open(abspath, "rb") as f:
            return b"\0" in f.read(8192)
    except OSError:
        return True


def status_entries(root):
    """path -> two-letter git status code, from a single porcelain call."""
    raw = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    out, toks, i = {}, raw.split("\0"), 0
    while i < len(toks):
        t = toks[i]
        if not t:
            i += 1
            continue
        code, path = t[:2], t[3:]
        if code[0] in ("R", "C"):
            # with -z the new path comes first and the original follows in its own token;
            # the new path is what gets reviewed
            i += 1
        out[path] = code
        i += 1
    return out


def ignored_convertibles(root):
    """Ignored files worth looking at: conversion sources only, never data trees."""
    specs = ["*." + e for e in sorted(policy.CONVERTIBLE)]
    raw = git(root, "ls-files", "-z", "--others", "--ignored", "--exclude-standard",
              "--", *specs, check=False)
    return [p for p in raw.split("\0") if p]


def load_manifest(root):
    path = os.path.join(root, MANIFEST)
    if not os.path.exists(path):
        return {"version": 1, "conversions": {}, "declined": {}}, True
    try:
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
        m.setdefault("conversions", {})
        m.setdefault("declined", {})
        return m, True
    except Exception:
        # Unreadable or conflicted: treat every source as new. Never authorization for anything.
        return {"version": 1, "conversions": {}, "declined": {}}, False


def manifest_state(root, rel, size, manifest):
    """new | unchanged | source_changed | output_changed | both_changed | output_missing"""
    entry = manifest["conversions"].get(rel)
    if not entry:
        declined_hash = manifest["declined"].get(rel)
        if declined_hash and sha(os.path.join(root, rel)) == declined_hash:
            return "declined", None
        return "new", None
    # size differs -> changed, with no hashing at all; otherwise hash (a same-size edit is
    # real and must not be missed)
    if entry.get("source_size") != size:
        src_same = False
    else:
        src_same = entry.get("source_hash") == sha(os.path.join(root, rel))
    out_rel = entry.get("output")
    out_abs = os.path.join(root, out_rel) if out_rel else None
    if not out_abs or not os.path.exists(out_abs):
        # the output was deliberately deleted: treat the source as new and offer once more
        return "new", None
    out_same = entry.get("output_hash") == sha(out_abs)
    if src_same and out_same:
        return "unchanged", entry
    if src_same and not out_same:
        return "output_changed", entry
    if not src_same and out_same:
        return "source_changed", entry
    return "both_changed", entry


STATE_DECISION = {
    "new":            ("convert", None),
    "source_changed": ("convert", "original changed since the last approved conversion"),
    "unchanged":      ("skip", "already converted and unchanged"),
    "declined":       ("exclude", "image conversion declined"),
    "output_changed": ("skip", "the converted copy was edited directly; left untouched"),
    "both_changed":   ("ask", "source and converted copy were both edited"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true", default=True)
    a = ap.parse_args()

    root = os.path.abspath(a.root)
    if policy.refused_root(root):
        print(json.dumps({"refused_root": True, "root": root,
                          "reason": "home, Desktop, Downloads, or a cloud-sync root is never "
                                    "a project folder"}, indent=2))
        return 0
    try:
        root = git(root, "rev-parse", "--show-toplevel").strip()
    except RuntimeError:
        print(json.dumps({"no_repository": True, "root": root}, indent=2))
        return 0
    if policy.refused_root(root):
        print(json.dumps({"refused_root": True, "root": root}, indent=2))
        return 0

    manifest, manifest_ok = load_manifest(root)
    tracked = set(p for p in git(root, "ls-files", "-z").split("\0") if p)
    statuses = status_entries(root)

    items = []
    seen = set()

    for rel, code in sorted(statuses.items()):
        seen.add(rel)
        abspath = os.path.join(root, rel)
        if code in (" D", "D ", "DD", "AD"):
            items.append({"path": rel, "size": None, "git_status": code,
                          "category": "deletion", "decision": "upload",
                          "reason": "deletion you made", "origin": "user_change"})
            continue
        if not os.path.exists(abspath):
            items.append({"path": rel, "size": None, "git_status": code,
                          "category": "missing", "decision": "exclude",
                          "reason": "file could not be read (not a deletion you made)",
                          "origin": "unknown"})
            continue
        if os.path.isdir(abspath) and not os.path.islink(abspath):
            nested = os.path.exists(os.path.join(abspath, ".git"))
            items.append({"path": rel, "size": None, "git_status": code,
                          "category": "nested-repo" if nested else "directory",
                          "decision": "exclude",
                          "reason": "nested repository; not recursed into" if nested
                                    else "directory reported by git; nothing to classify",
                          "origin": "user_change"})
            continue
        size, outside, unreadable, placeholder = probe(abspath, root)
        cat, dec, reason, kind = policy.classify(rel, size, outside, unreadable, placeholder)
        if cat == "unknown":
            if looks_binary(abspath):
                cat, dec, reason, kind = "unknown-binary", "exclude", \
                    "unsupported binary format", None
            else:
                cat, dec, reason, kind = "text", "upload", None, None
        item = {"path": rel, "size": size, "git_status": code, "category": cat,
                "decision": dec, "reason": reason, "origin": "user_change"}
        if kind:
            item["conversion"] = {"kind": kind,
                                  "output": policy.output_name(rel, kind)}
            state, _ = manifest_state(root, rel, size, manifest)
            item["manifest_state"] = state
            sdec, sreason = STATE_DECISION[state]
            if state != "new" or dec == "migrate":
                if state != "new":
                    item["decision"] = sdec if sdec != "convert" else dec
                    if sreason:
                        item["reason"] = sreason
        if rel in tracked and dec == "exclude" and cat == "csv":
            item["note"] = ("already tracked: keep the committed version and never commit a "
                            "deletion to untrack it")
        items.append(item)

    for rel in ignored_convertibles(root):
        if rel in seen:
            continue
        abspath = os.path.join(root, rel)
        size, outside, unreadable, placeholder = probe(abspath, root)
        cat, dec, reason, kind = policy.classify(rel, size, outside, unreadable, placeholder)
        if not kind:
            continue
        state, _ = manifest_state(root, rel, size, manifest)
        sdec, sreason = STATE_DECISION[state]
        items.append({"path": rel, "size": size, "git_status": "!!", "category": cat,
                      "decision": dec if sdec == "convert" else sdec,
                      "reason": sreason or reason, "origin": "ignored_source",
                      "conversion": {"kind": kind, "output": policy.output_name(rel, kind)},
                      "manifest_state": state})

    counts = {}
    for i in items:
        counts[i["decision"] or "unknown"] = counts.get(i["decision"] or "unknown", 0) + 1

    # policy check on unpushed history: a disallowed blob in an earlier local commit cannot be
    # fixed by deleting it in the newest one
    unpushed_bad = []
    if git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
           check=False).strip():
        names = git(root, "diff", "--name-only", "-z", "@{u}..HEAD", check=False)
        for rel in set(x for x in names.split("\0") if x):
            cat, dec, reason, _ = policy.classify(rel, MB + 1 if policy.ext_of(rel) == "csv"
                                                  else 1)
            if dec == "exclude" and cat != "csv":
                unpushed_bad.append({"path": rel, "reason": reason})

    # ignored files, collapsed by directory and capped: a complete-enough report without
    # walking a 40 GB data tree
    ign = [x for x in git(root, "ls-files", "-z", "--others", "--ignored",
                          "--exclude-standard", "--directory", check=False).split("\0") if x]
    ignored_summary = {"count": len(ign), "entries": sorted(ign)[:500],
                       "truncated": len(ign) > 500,
                       "reason": "existing ignore rule"}

    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip()
    upstream = git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
                   check=False).strip()
    ahead = git(root, "log", "--oneline", "@{u}..HEAD", check=False).strip()

    print(json.dumps({
        "root": root, "branch": branch, "upstream": upstream or None,
        "unpushed_commits": len([l for l in ahead.splitlines() if l]),
        "manifest_ok": manifest_ok, "counts": counts,
        "unpushed_disallowed": unpushed_bad, "ignored_summary": ignored_summary,
        "items": items,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
