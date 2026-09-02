#!/usr/bin/env bash
# Release an update to a plugin already in this marketplace.
#
#   ./sync.sh <plugin-name> [SOURCE] [NEW_VERSION]
#
# SOURCE is a skill directory for skill plugins, or a single .md file for
# output-style plugins. Copies it in, optionally bumps the version in both
# manifests, runs the plugin's own tests when it has them, and validates.
set -euo pipefail

NAME="${1:?usage: ./sync.sh <plugin-name> [SOURCE] [NEW_VERSION]}"
SRC="${2:-}"
VERSION="${3:-}"
REPO="$(cd "$(dirname "$0")" && pwd)"
DEST="$REPO/plugins/$NAME/skills/$NAME"
STYLES="$REPO/plugins/$NAME/output-styles"

if [ -d "$DEST" ]; then
  KIND=skill
elif [ -d "$STYLES" ]; then
  KIND=style
else
  echo "unknown plugin $NAME (see $REPO/plugins/)" >&2; exit 1
fi

if [ -z "$SRC" ]; then
  case "$KIND:$NAME" in
    skill:paperbase) SRC="$HOME/EvoErrorDetector/.claude/skills/paperbase" ;;
    style:*)         SRC="$HOME/.claude/output-styles/$NAME.md" ;;
  esac
fi

if [ -n "$SRC" ] && [ "$KIND" = skill ]; then
  [ -f "$SRC/SKILL.md" ] || { echo "no SKILL.md in $SRC" >&2; exit 1; }
  rsync -a --delete --exclude '__pycache__/' --exclude '*.pyc' --exclude '.DS_Store' \
        "$SRC/" "$DEST/"
  echo "synced $SRC -> $DEST"
elif [ -n "$SRC" ]; then
  [ -f "$SRC" ] || { echo "no such output style file: $SRC" >&2; exit 1; }
  cp "$SRC" "$STYLES/$(basename "$SRC")"
  echo "synced $SRC -> $STYLES/$(basename "$SRC")"
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

if [ -f "$DEST/tests/run_tests.py" ]; then python3 "$DEST/tests/run_tests.py" | tail -2; fi
claude plugin validate "$REPO" | tail -2
claude plugin validate "$REPO/plugins/$NAME" | tail -2
echo
echo "Next:  git -C $REPO add -A && git -C $REPO commit -m '$NAME ${VERSION:-update}' && git -C $REPO push"
echo "Users: /plugin marketplace update itamar-tools && /plugin update $NAME"
