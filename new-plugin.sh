#!/usr/bin/env bash
# Add a new skill to this marketplace as its own plugin.
#
#   ./new-plugin.sh <plugin-name> [SOURCE_SKILL_DIR]
#
# With SOURCE_SKILL_DIR, copies an existing skill folder in. Without it, scaffolds a
# SKILL.md stub for you to fill in. Then edit nothing else: the marketplace entry is
# appended automatically and validated.
set -euo pipefail

NAME="${1:?usage: ./new-plugin.sh <plugin-name> [SOURCE_SKILL_DIR]}"
SRC="${2:-}"
REPO="$(cd "$(dirname "$0")" && pwd)"
DIR="$REPO/plugins/$NAME"

[[ "$NAME" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] || { echo "plugin name must be kebab-case" >&2; exit 1; }
[ -e "$DIR" ] && { echo "$DIR already exists" >&2; exit 1; }

mkdir -p "$DIR/.claude-plugin" "$DIR/skills"
if [ -n "$SRC" ]; then
  [ -f "$SRC/SKILL.md" ] || { echo "no SKILL.md in $SRC" >&2; exit 1; }
  rsync -a --exclude '__pycache__/' --exclude '*.pyc' --exclude '.DS_Store' \
        "$SRC/" "$DIR/skills/$NAME/"
else
  mkdir -p "$DIR/skills/$NAME"
  cat > "$DIR/skills/$NAME/SKILL.md" <<EOF
---
name: $NAME
description: WHAT it does, and WHEN Claude should use it. Be specific - this text is the
  only thing Claude sees when deciding whether to load the skill.
---

# $NAME

Replace this with the workflow. Keep SKILL.md short and link to references/ for detail.
EOF
fi

DESC="$(python3 - "$DIR/skills/$NAME/SKILL.md" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
m = re.search(r"^description:\s*(.+?)(?=\n[a-zA-Z_-]+:|\n---)", text, re.S | re.M)
print(" ".join((m.group(1) if m else "").split())[:300])
PY
)"

python3 - "$REPO" "$NAME" "$DESC" <<'PY'
import json, sys
repo, name, desc = sys.argv[1], sys.argv[2], sys.argv[3]
manifest = repo + "/.claude-plugin/marketplace.json"
with open(manifest) as fh:
    data = json.load(fh)
if any(p["name"] == name for p in data["plugins"]):
    sys.exit("plugin %s is already listed" % name)
data["plugins"].append({"name": name, "source": "./plugins/%s" % name,
                        "description": desc, "version": "0.1.0"})
data["plugins"].sort(key=lambda p: p["name"])
with open(manifest, "w") as fh:
    json.dump(data, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
with open(repo + "/plugins/%s/.claude-plugin/plugin.json" % name, "w") as fh:
    json.dump({"name": name, "version": "0.1.0", "description": desc,
               "author": data["owner"]}, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
print("added %s to the marketplace at version 0.1.0" % name)
PY

claude plugin validate "$REPO" | tail -2
claude plugin validate "$DIR" | tail -2
echo
echo "Next:  git -C $REPO add -A && git -C $REPO commit -m 'Add $NAME plugin' && git -C $REPO push"
echo "Users: /plugin marketplace update itamar-tools  &&  /plugin install $NAME@itamar-tools"
