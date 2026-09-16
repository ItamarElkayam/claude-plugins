#!/usr/bin/env python3
"""Execute an approved plan: materialize outputs, commit only approved paths, integrate, push.

Refuses anything the plan does not authorize. Never resets, cleans, checks out over user
work, force-pushes, or uses `git add -A`.
"""
import argparse, hashlib, json, os, shutil, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import policy

MANIFEST = ".synctogit.json"


def git(root, *args, check=True):
    p = subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), (p.stderr or p.stdout).strip()))
    return p


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def inside(root, path):
    a = os.path.abspath(os.path.join(root, path))
    return (a == root or a.startswith(root + os.sep)) and ".git" not in a.split(os.sep)


def load_manifest(root):
    p = os.path.join(root, MANIFEST)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                m = json.load(f)
            m.setdefault("conversions", {})
            m.setdefault("declined", {})
            return m
        except Exception:
            pass
    return {"version": 1, "conversions": {}, "declined": {}}


def write_manifest(root, m):
    """One entry per line, sorted, so simultaneous conversions conflict readably."""
    lines = ['{', '  "version": 1,', '  "conversions": {']
    conv = sorted(m["conversions"].items())
    for i, (k, v) in enumerate(conv):
        lines.append('    %s: %s%s' % (json.dumps(k), json.dumps(v, sort_keys=True),
                                       "," if i < len(conv) - 1 else ""))
    lines.append('  },')
    lines.append('  "declined": {')
    dec = sorted(m["declined"].items())
    for i, (k, v) in enumerate(dec):
        lines.append('    %s: %s%s' % (json.dumps(k), json.dumps(v),
                                       "," if i < len(dec) - 1 else ""))
    lines += ['  }', '}', '']
    with open(os.path.join(root, MANIFEST), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--plan", required=True, help="approved plan JSON")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--no-integrate", action="store_true")
    a = ap.parse_args()

    root = os.path.abspath(git(a.root, "rev-parse", "--show-toplevel").stdout.strip())
    with open(a.plan, encoding="utf-8") as f:
        plan = json.load(f)
    rep = {"refused": [], "warnings": [], "committed": [], "left_out": [],
           "archived": [], "conflicts": [], "pushed": False, "actions": []}

    # ---- guardrail validation, before touching anything -------------------
    for mv in plan.get("archive_moves", []):
        if not mv.get("consent"):
            rep["refused"].append("archive move of %s: no specific consent" % mv.get("src"))
        if not inside(root, mv.get("dest", "")) or not inside(root, mv.get("src", "")):
            rep["refused"].append("archive move outside the project: %s" % mv.get("src"))
        if os.path.exists(os.path.join(root, mv.get("dest", ""))):
            rep["refused"].append("archive destination already exists: %s" % mv.get("dest"))
    for key in ("delete", "deletions", "cleanup", "remove"):
        if plan.get(key):
            rep["refused"].append("plan contains an unauthorized %s instruction" % key)

    # Word/Excel: publishing the converted file and archiving the original are ONE migration.
    consented = set(mv["src"] for mv in plan.get("archive_moves", []) if mv.get("consent"))
    for out in plan.get("outputs", []):
        if out.get("requires_archive_consent") and out["src"] not in consented:
            rep["refused"].append(
                "migration of %s: publishing %s requires the archive move to be approved too; "
                "without it the migration stays incomplete and nothing is published"
                % (out["src"], out.get("output")))

    if rep["refused"]:
        print(json.dumps(rep, indent=2))
        return 2

    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip()
    if branch == "HEAD":
        rep["refused"].append("detached HEAD: no branch to sync. Ask the user which branch "
                              "this work belongs on before committing.")
        print(json.dumps(rep, indent=2))
        return 2

    approved = [p for p in plan.get("approved_paths", []) if inside(root, p)]

    # pre-existing staged work that we were not asked to sync stays staged and untouched
    staged = set(x for x in git(root, "diff", "--name-only", "--cached").stdout.split("\n") if x)
    rep["preexisting_staged_kept"] = sorted(staged - set(approved))

    if a.dry_run:
        rep["actions"] = ["would commit: %s" % ", ".join(approved)]
        print(json.dumps(rep, indent=2))
        return 0

    manifest = load_manifest(root)

    # ---- materialize approved conversion outputs -------------------------
    for out in plan.get("outputs", []):
        staged_file, dest_rel = out["staged"], out["output"]
        if not inside(root, dest_rel):
            rep["refused"].append("output outside the project: %s" % dest_rel)
            continue
        dest = os.path.join(root, dest_rel)
        if os.path.exists(dest):
            known = manifest["conversions"].get(out["src"], {})
            if known.get("output") != dest_rel or known.get("output_hash") != sha(dest):
                rep["refused"].append(
                    "%s already exists and is not a previous output of %s; refusing to "
                    "overwrite it" % (dest_rel, out["src"]))
                continue
        os.makedirs(os.path.dirname(dest) or root, exist_ok=True)
        shutil.copy2(staged_file, dest)
        manifest["conversions"][out["src"]] = {
            "source_hash": out["source_hash"], "source_size": out["source_size"],
            "output": dest_rel, "output_hash": sha(dest)}
        manifest["declined"].pop(out["src"], None)
        approved.append(dest_rel)
        rep["actions"].append("wrote %s" % dest_rel)

    for d in plan.get("declined", []):
        manifest["declined"][d["src"]] = d["source_hash"]
        rep["actions"].append("recorded declined conversion for %s" % d["src"])

    # ---- archive moves: copy, verify, only then remove the source --------
    for mv in plan.get("archive_moves", []):
        s, d = os.path.join(root, mv["src"]), os.path.join(root, mv["dest"])
        os.makedirs(os.path.dirname(d), exist_ok=True)
        before = sha(s)
        shutil.copy2(s, d)
        if sha(d) != before:
            rep["refused"].append("archive copy of %s did not verify; source left in place"
                                  % mv["src"])
            continue
        os.remove(s)
        rep["archived"].append({"src": mv["src"], "dest": mv["dest"]})
        if mv["src"] in git(root, "ls-files", "-z").stdout.split("\0"):
            approved.append(mv["src"])          # a tracked original leaving the repo

    if plan.get("outputs") or plan.get("declined"):
        write_manifest(root, manifest)
        approved.append(MANIFEST)

    # ---- generated attributes -------------------------------------------
    if plan.get("gitignore_template"):
        path = os.path.join(root, ".gitignore")
        if not os.path.exists(path):
            open(path, "w", encoding="utf-8").write(policy.IGNORE_TEMPLATE)
            approved.append(".gitignore")
    if plan.get("gitattributes"):
        path = os.path.join(root, ".gitattributes")
        if not os.path.exists(path):
            open(path, "w", encoding="utf-8").write("* text=auto\n")
            approved.append(".gitattributes")

    # ---- stage and commit only approved paths ---------------------------
    approved = sorted(set(approved))
    if not approved:
        # only remote updates, or everything left out: still integrate, never commit nothing
        rep["warnings"].append("nothing approved; no commit made")
    elif (git(root, "add", "--", *approved) and
          not git(root, "diff", "--cached", "--quiet", "--", *approved,
                  check=False).returncode):
        rep["warnings"].append("approved paths hold no change; no commit made")
    else:
        msg = plan.get("message") or "Update project files"
        git(root, "commit", "-m", msg, "--", *approved)
        rep["committed"] = approved
        rep["commit"] = git(root, "rev-parse", "HEAD").stdout.strip()[:12]

    # ---- integrate ------------------------------------------------------
    if not a.no_integrate and git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name",
                                  "@{u}", check=False).returncode == 0:
        pull = git(root, "pull", "--no-rebase", "--no-edit", check=False)
        if pull.returncode != 0:
            conf = git(root, "diff", "--name-only", "--diff-filter=U", check=False).stdout
            rep["conflicts"] = [x for x in conf.split("\n") if x]
            rep["integration"] = "conflict: merge left in progress, nothing discarded"
            rep["note"] = ("resolve with the user; never reset, checkout over, or force. "
                           "Both versions are preserved in the working tree.")
            print(json.dumps(rep, indent=2))
            return 3
        rep["integration"] = "clean"

    # ---- push -----------------------------------------------------------
    if not a.no_push:
        push = git(root, "push", check=False)
        if push.returncode == 0:
            rep["pushed"] = True
        else:
            rep["pushed"] = False
            rep["push_error"] = (push.stderr or push.stdout).strip()[-2000:]
            rep["status"] = "Saved locally; upload incomplete"
    print(json.dumps(rep, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
