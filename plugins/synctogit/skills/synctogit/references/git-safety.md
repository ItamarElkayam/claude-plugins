# Git safety

## Never

`git reset --hard` · `git clean` · `git checkout --` · `git restore` against user work ·
`git push --force` · `git add -A` as the policy mechanism · history rewriting · `assume-unchanged`
or `skip-worktree` to hide an excluded file

If a merge or pull is stuck, that is a conflict to surface — not a thing to clear.

## Scope and target

- Existing repo: keep the current branch and its unambiguous upstream
- New repo: a conventional initial branch chosen at setup; no routine branch menu
- Detached HEAD, conflicting remotes, a missing upstream, an unfinished merge, a nested repo or
  submodule: handle explicitly, never guess. Never recurse into a nested repo or submodule or
  treat its files as part of the outer project
- Never follow a symlink out of the project to upload its target — report it as a skip reason
- A path missing because of a read error, an undownloaded cloud file, or a failed scan is **not**
  evidence the user deleted it
- Respect existing repository hooks. Add none

## Stages

Identify → Inspect → Prepare → Review → Commit → Integrate → Push → Report

Fetch during preparation to surface problems early; fetching must not touch working files.
Nothing before approval may commit, push, move an original, or create the remote repository.

## Preserving the user's staged work

- Record HEAD and index state before you start
- Stage only reviewed, approved paths
- Cancellation or failure must leave pre-existing staged and unstaged work exactly as it was
- Approved files commit as they are on disk. A file edited while the review was open uploads its
  newer content — nothing is overwritten and nothing is lost, so this needs no special handling
- The one place a fresh check matters is a converted output: never overwrite one the user edited.
  The manifest states in `conversions.md` cover that

## Conflicts

Let Git merge first. Help only when the combined result is unambiguous and preserves both
sides' work.

| Situation | Do |
| --- | --- |
| Independent edits in different places | Let Git combine them |
| Clearly independent additions to a simple list | May combine if order and meaning are unaffected; report it |
| Different values for the same CSV cell or parameter | Ask which is intended |
| Deletion on one side, edits on the other | Ask |
| Binary or image conflict | Keep both inputs, ask for a choice, never blend |
| CSV rows reordered with no reliable identity | Ask; never match rows by position |
| Ambiguous prose or code | Ask a focused question with the alternatives |

Normal integration *may* update or remove clean tracked files to match user-authored Git
changes — that is legitimate sync, not a guardrail breach. What is forbidden is a change you
invented, and the loss of conflicting local work.

A clean textual merge is not proof of scientific correctness. Do not imply it is.

Preserve both versions, report any automatic resolution, never force-push around a conflict,
never pick "ours" or "theirs" wholesale. If resolving a conflict materially changes content
after the final review, show it for approval — but don't re-confirm an identical plan.

## Excluded local files vs incoming changes

Rely on git's own behavior. Git already refuses to pull when an incoming file would overwrite
an untracked local file — it stops with "untracked working tree file would be overwritten by
merge" and changes nothing. Report that as a conflict and ask the user; do not work around it.

One known limitation, accepted deliberately: if a path was tracked, was untracked on one side,
and edited on the other, git resolves the modify/delete conflict by writing the incoming
version into the working tree, overwriting an untracked local file at that path. Nothing in
the skill untracks files, so reaching this state takes a manual `git rm --cached`. If a user
reports losing a file that way, explain what happened rather than guessing at a recovery — the
content was never committed, so git has no copy of it.

## Push failures

Keep the local commit. Report **"Saved locally; upload incomplete"** — never "synced". Name the
file the remote objected to when Git identifies it (this is how a file over GitHub's 100 MiB
limit surfaces).

A retry reuses existing work: no duplicate commits, no repeated conversions, no broadened
sharing. If the remote advanced, fetch and integrate under the rules above. Re-approval is for
a materially changed operation, not for resending identical bytes.

## Unpushed history

Inspect outgoing commits, not just working files. A disallowed blob in an earlier unpushed
commit is not fixed by deleting it in the newest one. Stop that push, explain, preserve the
commits. No rewriting, no force-push, no claiming a safe subset can go without its ancestors.

## Interruption

On the next run, re-inspect real Git and filesystem state. Never infer that a push succeeded.
Two syncs at once in the same folder are not guarded against: git's own index lock stops the
second one, and reporting its error is enough.

## Undo

`/synctogit undo` creates a revert commit, after the same explicit confirmation as a sync.
It never rewrites or force-pushes. If the last sync has been built upon locally or remotely,
say so and revert only what reverses cleanly; ask when the result would be ambiguous. Undo does
not restore an archived original to its old path, delete files, or reverse a conversion
decision — it is a Git-level revert.
