---
name: synctogit
description: Sync a local lab project to GitHub under the RechaviLab file policy. Prepares eligible changes, offers conversions for images/PDF/Word, gets explicit approval in one review, then commits, integrates collaborators' work, and pushes. Excel workbooks are always excluded, never converted. Also handles repository creation, cloning, and undo. Invoke manually as /synctogit.
disable-model-invocation: true
---

# /synctogit

Sync a local project to a RechaviLab GitHub repository under the lab file policy, in three
user-visible steps: run it, review a summary, confirm.

## The guardrail — read before doing anything

**Never independently delete, overwrite, or modify a user's files or data.** Ordinary Git
synchronization of changes *users* made is fine and expected.

Specifically, in this workflow you must never:

- run `git reset --hard`, `git clean`, `git checkout --`, `git restore`, or `git push --force`
- delete an original because a conversion succeeded or another copy exists
- delete or move a file because it is large, ignored, unsupported, duplicated, or looks unneeded
- commit a deletion to "untrack" a file that policy excludes
- discard staged or unstaged work to make a pull, merge, or retry succeed
- overwrite a converted output that the user edited themselves
- treat "Confirm sync" as permission for an archive move or any cleanup

Exclusion means *do not upload*. It never means remove local data. If an operation cannot be
done without breaking this rule, stop and explain. This is a discipline you follow, not a
sandbox around you — do not tell the user they are protected by anything stronger.

Every file change you make must trace to one of: a change the user already made, an incoming
Git change, a conversion output the user approved, a specifically approved archive move, or a
generated `.gitignore` / `.gitattributes` / `.synctogit.json` change shown in the review.
Anything else is refused.

## Flow

### 0. Setup check (first run in a repo, or if something is missing)

Run `scripts/setup_check.py`. It reports what's missing. Act on it:

- no `git` → tell the user how to install it, stop
- no `user.name` / `user.email` → **ask the user for both and set them.** Without these
  `git commit` fails outright
- `gh` not authenticated → run `gh auth login` and let them finish in the browser
- no `.gitattributes` → create it with `* text=auto` and include it in the reviewed commit
- no `.gitignore` → pass `gitignore_template: true` once, which writes the lab type-level
  rules. After that it is the user's file: never rewrite it, and never add per-file entries
- missing converters → say which, and which file types are unsupported here; continue

### 1. Scan

```
python3 scripts/scan.py --root <repo root>
```

It emits JSON: every candidate file with a category, a decision, a reason, and the conversion
state from `.synctogit.json`. **Trust its decisions.** Do not re-derive size limits or
extension rules yourself — that is the point of the script.

If it reports `refused_root`, the folder is a home/Desktop/Downloads/cloud root. Stop and ask
for the real project folder.

If there is no repository yet, see `references/repos.md` (creation and cloning).

### 2. Prepare conversions

For each item with `decision: "convert"`, run `scripts/convert.py`. Outputs go to a staging
directory — nothing touches the project yet. Rules in `references/conversions.md`; read it
before your first conversion in a session. Key points:

- a source whose hash changed gets a **fresh** preview even if an output already exists
- if the *output* was edited, leave it alone
- if both changed, ask
- Excel workbooks (`.xlsx`, `.xlsm`, `.xls`, `.ods`) are always excluded, at any size. No CSV
  migration is offered, ever — treat them exactly like PowerPoint
- Word: publishing the converted Markdown and archiving the original `.docx`/`.doc` into
  `_archive/` are **one** migration. If the archive move is declined, publish nothing and leave
  the original in place

### 3. Review and approve

```
python3 scripts/review.py --plan <plan.json>
```

Serves one loopback page: image previews side by side, a checkbox per file (checked by
default, so a user with five changes can upload four), exclusions with reasons, any archive
move, and the final action. It writes the user's selections and exits.

If no images need a decision, skip the page and confirm in the session instead — show
repository, folder, the one-line commit message, file counts, and ask.

Never auto-approve a conversion because the page failed to open. Offer the URL as text, or
offer to sync without conversions.

### 4. Commit, integrate, push

In this order:

1. Materialize approved outputs, apply approved `.gitignore` / `.gitattributes` /
   `.synctogit.json` changes, perform an approved archive move (verify the copy first).
2. `git add` **only the approved paths**. Never `git add -A`.
3. Commit with a one-line message you write from the changes — concise, derived from paths and
   their nature, not from file contents. The user does not edit it.
4. `git pull --no-rebase`. On conflict, follow `references/git-safety.md`. Never force anything.
5. `git push`.

Re-check the working tree immediately before step 1. If a reviewed file changed since the
review opened, drop it from the plan and say so.

### 5. Report

Say what was uploaded, what came down, what was excluded and why, and what the user left out.
Distinguish these three: *policy exclusion*, *unchanged and already in sync*, *failed push*.
Never call a failed push "synced" — say "Saved locally; upload incomplete."

Show once, beside applicable exclusions: "Large files and important experimental data should
be saved directly to the lab server." PowerPoint is labelled "Managed locally only" and is
never in that group.

## Subcommands

- `/synctogit` — the flow above
- `/synctogit undo` — `scripts/undo.py --root <root>` reverts the last sync with a new commit,
  after the same confirmation. Run it with `--describe` first and show the user what will be
  reversed. Exit 2 means it needs a decision (a merge commit); exit 3 means the revert
  conflicts — nothing was discarded, and `git revert --abort` restores the prior state.
- `/synctogit review skipped` — re-offer conversions the user previously declined

## References

Read the relevant file before acting in that area; don't work from memory.

| File | When |
| --- | --- |
| `references/file-policy.md` | What is allowed, excluded, or converted, and why |
| `references/conversions.md` | Image, PDF, Word, Excel rules and the `.synctogit.json` manifest |
| `references/git-safety.md` | Conflicts, staged work, push failures, unpushed history |
| `references/repos.md` | Choosing, creating, cloning a repository; organization access |

## Scripts

| Script | Role |
| --- | --- |
| `scripts/setup_check.py` | What is missing before a sync can work |
| `scripts/scan.py` | Per-file category, decision, reason, conversion state. Side-effect free |
| `scripts/convert.py` | One conversion into a staging directory; never touches the source |
| `scripts/review.py` | The loopback review page; writes the user's selections |
| `scripts/apply.py` | Executes an approved plan under the guardrail |
| `scripts/undo.py` | Revert commit for the last sync |
| `scripts/policy.py` | The file policy itself — extension tables, size limit, output names |

`tests/run_tests.py` runs 77 tests against throwaway mock repositories;
`tests/mutation_test.py` breaks each guarantee on purpose to prove the tests can fail.
Run both after changing any script.
