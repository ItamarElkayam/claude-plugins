# File policy

Fixed lab policy. No per-repository overrides and no per-file exceptions. `scripts/scan.py`
implements all of it — read this to explain decisions to a user, not to re-derive them.

"1 MB" means 1,000,000 bytes. Exactly 1,000,000 is allowed; more is not.

Decisions come from path, extension, format validity, and size. **File contents are never
inspected for credentials, personal data, or data-access class.** That is out of scope by
decision.

## Matrix

| Category | Rule | What reaches Git |
| --- | --- | --- |
| Markdown, prose, source code, text config, Jupyter notebooks | Allow, no lab cap | The original |
| CSV | Allow at or below 1 MB | The same editable CSV |
| Excel workbook | Exclude original; offer one-time CSV migration | Validated CSV(s), each ≤1 MB |
| Word document | Exclude original; offer one-time Markdown migration | Markdown, plus separately eligible assets |
| JPEG/PNG/WebP/GIF at or below 1 MB | Allow if valid | The original |
| TIFF and other heavy image sources | Exclude source at any size; offer a viewing conversion | Only an approved viewing copy |
| Viewing images above 1 MB | Exclude source; offer compression | Only an approved copy ≤1 MB |
| PDF | Compress automatically; no preview, no 1 MB ceiling | Smaller valid PDF, else the original |
| PowerPoint | Exclude at any size | Nothing |
| Experimental raw formats | Exclude at any size | Nothing |
| ZIP, video, `.npy`, other unhandled binary | Exclude at any size | Nothing |
| Already ignored by a user rule | Respect it | Nothing |

Notebooks are ordinary text by decision: `.ipynb` uploads as-is, outputs intact, no stripping.
Their diffs are hard to read and they can get large. That is accepted.

TIFF is a conversion source at every size, including a 200 KB one, because GitHub cannot
preview it. This is deliberate, not a size bug.

Evaluate specific rules before generic ones: CSV is not exempt for being text, and FASTQ stays
excluded even though its contents are textual. An ordinary JSON file is *not* excluded merely
for being JSON.

## Experimental formats — excluded at any size

`.fastq` `.fq` `.sam` `.bam` `.cram` `.fast5` `.pod5` `.czi` `.lif` `.nd2` `.ims` `.fcs`
`.mzML` `.mzXML`

Compressed variants (`.fastq.gz`) and any capitalization are included. An archive stays
excluded even when what's inside would be allowed.

## GitHub's own limits

Not pre-checked, except for PDFs. A blob over 100 MiB or a push over 2 GB is rejected by the
remote; when that happens the local commit stays and the user is told the upload did not
complete, naming the file if Git identifies it. See `git-safety.md`.

## Skip reasons to use in the report

Raw experimental format · CSV exceeds 1 MB · image conversion declined · unsupported binary
format · PDF still exceeds the hosting limit · original replaced by an approved migration ·
existing ignore rule · conversion failed or unverifiable · PowerPoint: managed locally only ·
left out by you in this sync · symlink pointing outside the project · cloud file not present
locally · file could not be read · upload did not complete

## A tracked file that becomes ineligible

For example a tracked CSV grows past 1 MB:

- keep the new content out of the commit
- preserve the local file exactly as it is
- leave the previously committed version in Git
- report it as skipped and say the latest contents were not uploaded
- **add no `.gitignore` entry** — it does nothing for an already-tracked file
- never commit a deletion to untrack it
- carry on with other eligible files

The file will keep showing as modified in Git. That is correct; do not hide it with
`assume-unchanged` or `skip-worktree`.

## `.gitignore`

Written once at setup from the lab template in `scripts/policy.py` (`IGNORE_TEMPLATE`) —
type-level rules only: raw formats, slides, heavy image sources, `_archive/`.

After that it belongs to the user. Never rewrite it, never add per-file entries, never remove
a user's rule to force an upload, and never touch global git config or the global ignore file.

There is no need for per-file entries: the skill only ever stages the exact paths the user
approved in the review, so an unapproved file cannot be uploaded whether or not it is ignored.
A file excluded by size stays in the exclusion report each run, with its reason, which is
where the user should see it.

Conversion decisions are remembered in `.synctogit.json`, never in `.gitignore` — so a photo
you refuse to convert is refused once, and an ignore entry never implies a decision was made.
