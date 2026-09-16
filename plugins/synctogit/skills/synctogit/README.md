# synctogit

A Claude Code skill: sync a local lab project to a RechaviLab GitHub repository under the lab
file policy, in three visible steps — run it, review, confirm.

Built to the specification in `synctogit_implementation_spec_v1.2.md`.

## Install (per lab member)

```bash
git clone https://github.com/RechaviLab/synctogit ~/.claude/skills/synctogit
```

Then in any project: `/synctogit`

Updates: `git -C ~/.claude/skills/synctogit pull`

A plain skill folder keeps the command named exactly `/synctogit`. Packaging it as a plugin
later would rename it to `/pluginname:synctogit`, so don't.

## Requirements

- `git` (required) and a configured `user.name` / `user.email` — the skill asks on first run
- `gh` for repository listing and creation
- Optional converters, each enabling one file type: Pillow (images), Ghostscript (PDF),
  Pandoc (Word), openpyxl (Excel). Missing ones exclude that type and say so.

## The rule everything else serves

The skill never independently deletes, overwrites, or modifies user files. Ordinary Git
synchronization of changes *users* made is expected and allowed. Exclusion means "not
uploaded", never "removed".

This is enforced by the workflow and by `scripts/apply.py`, which refuses any file effect that
does not trace to a user change, an incoming Git change, an approved conversion, a specifically
approved archive move, or a reviewed metadata change. It is not a sandbox: the skill runs in an
ordinary Claude Code session with shell access, so don't tell users they are protected by more
than this.

## Layout

```
SKILL.md            what Claude reads and follows
references/         file policy, conversions, git safety, repositories
scripts/            policy, scan, convert, review, apply, undo, setup check
tests/              77 tests over mock repos + a mutation suite
```

Two files the skill owns inside a project: `.synctogit.json` (conversion manifest, committed,
so clones behave identically) and `_archive/` (originals from an approved migration, never
uploaded).

## Tests

```bash
python3 tests/run_tests.py            # 77 tests, ~30s, throwaway repos in a temp dir
python3 tests/run_tests.py --only=guardrail
python3 tests/mutation_test.py        # breaks 15 guarantees; each must turn a test red
```

A local bare repository stands in for GitHub, so push, pull, conflict, and collaborator
scenarios run for real with no credentials and no network.
