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

## Excel → CSV

**Multi-sheet workbooks ask first.** Say plainly that a multi-sheet workbook cannot be carried
into Git as one file, then offer:

1. **Split** — one CSV per sheet, named after the sheet, each validated against 1 MB separately
2. **Skip** — the workbook stays excluded and unchanged; other files continue

Never silently export one sheet and drop the rest.

Then, for a single sheet or after a split is chosen:

1. Convert with openpyxl
2. Validate each CSV and enforce 1 MB per output
3. Explain what workbook features are lost
4. Get explicit approval for archiving the workbook
5. Move the original into `_archive/` — verify the copy before removing the source
6. Leave only the CSV(s) as the active tables

The workbook is never uploaded at any size.

- Formula results must be real values. Some readers expose only cached results; a missing cache
  must never become a blank cell. Use a validated recalculation route or report that the
  conversion cannot be completed. Never run macros or fetch external links to invent values
- Preserve textual identifiers, empty values, quoting, Unicode, significant precision
- New exports: UTF-8, comma delimiters, correct quoting, original row and column order
- An ordinary sync must never reformat, sort, or coerce an existing tracked CSV
- Declined or failed migration: original intact and excluded
- Never delete an archived original or send it to the lab server
- A workbook appearing later beside an existing CSV: flag the ambiguity, never export over it
- A later CSV edit is just an edit to the tracked CSV. There is no reverse conversion

## CSV editing caveat

Do not install a CSV editor. If asked: Excel can save CSV, but its import can silently change
identifiers, date-like strings, and leading zeros — and a valid CSV with a clean diff does not
prove those changes were intended. The skill cannot repair that from the saved file. Say so
rather than implying protection.

## `_archive/`

Inside the project, excluded from upload, visible to the user so their own folder backups cover
it. Keep the original filename, adding a suffix only to avoid a collision. Show the path in the
result. Never delete its contents, on any path — cancellation, retry, or error.
