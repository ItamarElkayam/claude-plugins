# Conversions and the manifest

## `.synctogit.json` — the conversion manifest

One tracked file at the repository root. Committed, so a teammate's clone and the user's
second machine behave identically. There is **no local cache or state directory anywhere.**

```json
{
  "version": 1,
  "conversions": {
    "figures/figure.tiff": {
      "source_hash": "a1b2c3…", "source_size": 48210944,
      "output": "figures/figure.preview.jpg", "output_hash": "c3d4e5…"
    }
  },
  "declined": { "figures/draft.tiff": "9f8e7d…" }
}
```

Comparing recorded hashes with the current files gives four states:

| Source | Output | Behavior |
| --- | --- | --- |
| same | same | Already done. Do nothing, ask nothing. |
| **changed** | same | User edited the original → **offer a fresh preview.** Do not skip it because an output exists. |
| same | **changed** | User edited the converted copy → real user work. Never regenerate it. |
| **changed** | **changed** | Both edited → ask which wins. Overwrite neither. |

No entry at all = new, so ask. An entry whose output no longer exists also counts as new: the
user deleted that preview on purpose, so it is never recreated behind their back, and the
source is simply offered once more (a decline is then remembered as usual).

**Declining records the hash it applied to.** So a declined conversion stays declined for that
revision only — edit the original again and a fresh preview is offered. This is why hashes are
stored instead of an "already asked" flag.

Rules:

- Include manifest changes in the reviewed commit
- One entry per line, sorted by source path, so simultaneous conversions conflict readably
- Repository-relative paths, hashes, sizes and output names only — never absolute paths,
  machine names, or user identity
- Missing, unreadable, or conflicted manifest → treat every source as new and ask. It is never
  authorization to delete or regenerate anything
- Hash only conversion sources. Never hash `fast5/` or `.fastq` — they're excluded by extension
  before any read. A recorded size that differs short-circuits to "changed" with no hashing;
  when the size matches, hash anyway, because a same-size edit is real and must not be missed

## Images

- Aim for roughly 500 KB when still readable; hard ceiling 1 MB
- JPEG or WebP for photographs; PNG or lossless WebP for plots, text, line drawings
- Preserve aspect ratio, apply orientation correctly, show dimension changes in the preview
- Never crop, add labels, or alter scientific content
- Never overwrite the original
- Reject a broken or unverifiable output
- Output name is stable and derived from the source: `figure.tiff` → `figure.preview.jpg`, so
  later approved updates replace the same logical file
- If a meaningful view needs a chosen frame, channel, or page, ask one concrete question or
  leave the source excluded and say why. Never silently drop that dimension

If the user declines a fresh conversion: keep the existing copy in Git, record the declined
source hash, and report that Git's view is now behind the original.

## PDFs

- Compress the PDF itself (Ghostscript, image downsampling where useful). Never ZIP it
- No interactive preview, but show before/after size in the review
- No lab 1 MB ceiling for PDFs
- Keep the original if compression doesn't help
- Trust Ghostscript's own failure report, plus a non-empty output. Do not verify page counts
  by inspecting the bytes: modern PDFs compress that structure so the check cannot see it, and
  on older files it throws away good compressions
- Stable distinct name, e.g. `paper.git.pdf`. Upload one representation; never two competing
  tracked PDFs because compression was tried
- Signed or encrypted PDFs: upload a valid original or exclude. Never strip protections, never
  present a modified signature as intact
- Don't reprocess an unchanged PDF every run

## Word → Markdown

Markdown becomes the shared editable document. There is no recurring export and no
Markdown→Word sync.

- Offer a one-time conversion (Pandoc); basic formatting is enough
- Tell the user Markdown is the file to edit from now on
- **Publishing the Markdown and moving the `.docx` into `_archive/` are one migration.** If the
  archive move is declined, publish nothing: the `.docx` stays where it is, excluded and
  unchanged. Other files still sync. This is what stops two active copies existing with no
  indication of which to edit
- Embedded images and extracted assets follow their own policy; fix relative links when an
  asset lands under a different name; if an asset is excluded, say so rather than claiming a
  complete migration
- Never overwrite an existing, independently edited `.md` with a new export

## Excel

No conversion is offered. Every Excel workbook (`.xlsx`, `.xlsm`, `.xls`, `.ods`) is excluded at
any size, unconditionally — "Excel: managed locally only", the same treatment as PowerPoint.
Never run `convert.py --kind excel`, never offer a CSV migration, and never move a workbook into
`_archive/` as part of a migration (there is no migration to complete). The original stays where
it is, untouched.

## `_archive/`

Inside the project, excluded from upload, visible to the user so their own folder backups cover
it. Keep the original filename, adding a suffix only to avoid a collision. Show the path in the
result. Never delete its contents, on any path — cancellation, retry, or error.
