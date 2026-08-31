#!/usr/bin/env bash
# Sync the skill from a working copy into this marketplace repo, then validate.
#
#   ./sync-from-project.sh [SOURCE_SKILL_DIR] [NEW_VERSION]
#
# Defaults to the EvoErrorDetector project copy. With NEW_VERSION given, bumps the
# version in both manifests so `/plugin update` sees a new release.
set -euo pipefail

SRC="${1:-$HOME/EvoErrorDetector/.claude/skills/paperbase}"
VERSION="${2:-}"
REPO="$(cd "$(dirname "$0")" && pwd)"
DEST="$REPO/plugins/paperbase/skills/paperbase"

[ -f "$SRC/SKILL.md" ] || { echo "no SKILL.md in $SRC" >&2; exit 1; }

rsync -a --delete \
  --exclude '__pycache__/' --exclude '*.pyc' --exclude '.DS_Store' \
  "$SRC/" "$DEST/"
echo "synced $SRC -> $DEST"

if [ -n "$VERSION" ]; then
  python3 - "$REPO" "$VERSION" <<'PY'
import json, sys
repo, version = sys.argv[1], sys.argv[2]
for path, setter in (
    (repo + "/plugins/paperbase/.claude-plugin/plugin.json",
     lambda d: d.__setitem__("version", version)),
    (repo + "/.claude-plugin/marketplace.json",
     lambda d: d["plugins"][0].__setitem__("version", version)),
):
    with open(path) as fh:
        data = json.load(fh)
    setter(data)
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print("version -> %s in %s" % (version, path.rsplit("/", 3)[-1]))
PY
fi

python3 "$DEST/tests/run_tests.py" | tail -2
claude plugin validate "$REPO" | tail -2
claude plugin validate "$REPO/plugins/paperbase" | tail -2
echo
echo "Now:  git -C $REPO add -A && git -C $REPO commit -m 'paperbase ${VERSION:-update}' && git -C $REPO push"
