#!/usr/bin/env python3
"""Report what is missing before a sync can work. Changes nothing."""
import json, os, shutil, subprocess, sys, argparse


def run(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except (OSError, subprocess.TimeoutExpired):
        return 127, "", "not available"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    a = ap.parse_args()
    out = {"blocking": [], "ask_user": [], "notes": [], "converters": {}}

    if not shutil.which("git"):
        out["blocking"].append("git is not installed")
        print(json.dumps(out, indent=2))
        return 1

    for key, label in (("user.name", "name"), ("user.email", "email")):
        rc, val, _ = run(["git", "config", "--get", key])
        if rc != 0 or not val:
            out["ask_user"].append(
                "git %s is not set; ask the user for it and run: git config --global %s '<value>'"
                % (label, key))

    if not shutil.which("gh"):
        out["notes"].append("gh (GitHub CLI) not installed: repository listing and creation "
                            "are unavailable; plain git push still works")
    else:
        rc, _, _ = run(["gh", "auth", "status"])
        if rc != 0:
            out["ask_user"].append("gh is not authenticated; run: gh auth login")

    root = os.path.abspath(a.root)
    rc, top, _ = run(["git", "-C", root, "rev-parse", "--show-toplevel"])
    if rc == 0 and top:
        root = top
        out["repo_root"] = root
        if not os.path.exists(os.path.join(root, ".gitattributes")):
            out["notes"].append("no .gitattributes: create it containing '* text=auto' and "
                                "include it in the reviewed commit")
    else:
        out["notes"].append("not inside a git repository yet")

    for name, tool, enables in (
            ("pillow", None, "image compression and TIFF conversion"),
            ("ghostscript", "gs", "PDF compression"),
            ("pandoc", "pandoc", "Word to Markdown"),
            ("openpyxl", None, "Excel to CSV"),
            ("libreoffice", "soffice", "legacy .doc/.xls conversion")):
        if tool:
            ok = shutil.which(tool) is not None
        else:
            mod = {"pillow": "PIL", "openpyxl": "openpyxl"}[name]
            ok = subprocess.run([sys.executable, "-c", "import " + mod],
                                capture_output=True).returncode == 0
        out["converters"][name] = ok
        if not ok:
            out["notes"].append("%s missing: %s unavailable on this machine" % (name, enables))

    out["ready"] = not out["blocking"] and not out["ask_user"]
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
