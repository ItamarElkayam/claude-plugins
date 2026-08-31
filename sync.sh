#!/usr/bin/env bash
# Release an update to a plugin already in this marketplace.
#
#   ./sync.sh <plugin-name> [SOURCE_SKILL_DIR] [NEW_VERSION]
#
# Copies the skill in, optionally bumps the version in both manifests, runs the
# plugin's own tests when it has them, and validates the manifests.
set -euo pipefail

NAME="${1:?usage: ./sync.sh <plugin-name> [SOURCE_SKILL_DIR] [NEW_VERSION]}"
SRC="${2:-}"
VERSION="${3:-}"
REPO="$(cd "$(dirname "$0")" && pwd)"
DEST="$REPO/plugins/$NAME/skills/$NAME"

[ -d "$DEST" ] || { echo "unknown plugin $NAME (see $REPO/plugins/)" >&2; exit 1; }
if [ -z "$SRC" ] && [ "$NAME" = "paperbase" ]; then
  SRC="$HOME/EvoErrorDetector/.claude/skills/paperbase"
fi

if [ -n "$SRC" ]; then
  [ -f "$SRC/SKILL.md" ] || { echo "no SKILL.md in $SRC" >&2; exit 1; }
  rsync -a --delete --exclude '__pycache__/' --exclude '*.pyc' --exclude '.DS_Store' \
        "$SRC/" "$DEST/"
  echo "synced $SRC -> $DEST"
fi

if [ -n "$VERSION" ]; then
  python3 - "$REPO" "$NAME" "$VERSION" <<'PY'
import json, sys
repo, name, version = sys.argv[1], sys.argv[2], sys.argv[3]
path = repo + "/plugins/%s/.claude-plugin/plugin.json" % name
with open(path) as fh:
    data = json.load(fh)
data["version"] = version
with open(path, "w") as fh:
    json.dump(data, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
manifest = repo + "/.claude-plugin/marketplace.json"
with open(manifest) as fh:
    market = json.load(fh)
for entry in market["plugins"]:
    if entry["name"] == name:
        entry["version"] = version
with open(manifest, "w") as fh:
    json.dump(market, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
print("version -> %s in plugin.json and marketplace.json" % version)
PY
fi

[ -f "$DEST/tests/run_tests.py" ] && python3 "$DEST/tests/run_tests.py" | tail -2
claude plugin validate "$REPO" | tail -2
claude plugin validate "$REPO/plugins/$NAME" | tail -2
echo
echo "Next:  git -C $REPO add -A && git -C $REPO commit -m 'paperbase ${VERSION:-update}' && git -C $REPO push"
echo "Users: /plugin marketplace update itamar-tools && /plugin update $NAME"
