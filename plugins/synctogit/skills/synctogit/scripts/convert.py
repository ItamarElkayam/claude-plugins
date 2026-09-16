#!/usr/bin/env python3
"""Produce a conversion output in a staging directory. Never touches the source.

Usage:
  convert.py --kind image --src figures/f.tiff --root R --stage DIR
  convert.py --kind pdf   --src paper.pdf      --root R --stage DIR
  convert.py --kind word  --src doc.docx       --root R --stage DIR
  convert.py --kind excel --src book.xlsx      --root R --stage DIR [--sheets first|all]
  convert.py --kind excel --src book.xlsx      --root R --inspect
"""
import argparse, hashlib, json, os, shutil, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import policy

MB = policy.MB


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def fail(msg, **kw):
    print(json.dumps(dict({"ok": False, "error": msg}, **kw), indent=2))
    return 1


def do_image(src_abs, stage, rel):
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return fail("Pillow is not installed; image conversion unavailable")
    out = os.path.join(stage, os.path.basename(policy.output_name(rel, "image")))
    try:
        im = Image.open(src_abs)
        im = ImageOps.exif_transpose(im)          # apply orientation, never rotate content
        before = im.size
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGB")
        # never crop, never relabel: only scale and re-encode
        for scale in (1.0, 0.85, 0.7, 0.55, 0.45, 0.35, 0.25):
            w, h = int(before[0] * scale), int(before[1] * scale)
            work = im if scale == 1.0 else im.resize((max(w, 1), max(h, 1)), Image.LANCZOS)
            for q in (92, 85, 78, 70, 62):
                work.save(out, "JPEG", quality=q, optimize=True, progressive=True)
                if os.path.getsize(out) <= MB:
                    with Image.open(out) as chk:
                        chk.verify()
                    return ok(out, src_abs, rel, "image",
                              source_dims=list(before), output_dims=list(work.size),
                              quality=q)
    except Exception as e:                       # broken or unverifiable output is a failure
        return fail("image conversion failed: %s" % e)
    return fail("could not reach 1 MB without unacceptable loss")


def do_pdf(src_abs, stage, rel):
    if not shutil.which("gs"):
        return fail("Ghostscript (gs) is not installed; PDF compression unavailable")
    out = os.path.join(stage, os.path.basename(policy.output_name(rel, "pdf")))
    before = os.path.getsize(src_abs)
    p = subprocess.run(["gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5",
                        "-dPDFSETTINGS=/ebook", "-dNOPAUSE", "-dQUIET", "-dBATCH",
                        "-dDetectDuplicateImages=true", "-sOutputFile=" + out, src_abs],
                       capture_output=True, text=True)
    # Ghostscript reports its own failures; an empty output is the other way it can go wrong
    if p.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
        return fail("PDF compression failed; use the original if it is uploadable",
                    detail=(p.stderr or "")[-500:])
    if os.path.getsize(out) >= before:
        return ok(None, src_abs, rel, "pdf", use_original=True,
                  note="compression did not help; upload the original",
                  source_bytes=before)
    return ok(out, src_abs, rel, "pdf", source_bytes=before)


def do_word(src_abs, stage, rel):
    if not shutil.which("pandoc"):
        return fail("Pandoc is not installed; Word conversion unavailable")
    out = os.path.join(stage, os.path.basename(policy.output_name(rel, "word")))
    media = os.path.join(stage, "media")
    p = subprocess.run(["pandoc", src_abs, "-t", "gfm", "--wrap=none",
                        "--extract-media=" + media, "-o", out],
                       capture_output=True, text=True)
    if p.returncode != 0 or not os.path.exists(out):
        return fail("Word conversion failed", detail=(p.stderr or "")[-500:])
    assets = []
    for dirpath, _, names in os.walk(media):
        for n in names:
            assets.append(os.path.relpath(os.path.join(dirpath, n), stage))
    return ok(out, src_abs, rel, "word", assets=sorted(assets),
              reminder="publishing this .md and archiving the .docx are ONE migration")


def do_excel(src_abs, stage, rel, sheets, inspect):
    try:
        import openpyxl
    except ImportError:
        return fail("openpyxl is not installed; Excel conversion unavailable")
    import csv
    try:
        wb = openpyxl.load_workbook(src_abs, data_only=True, read_only=True)
    except Exception as e:
        return fail("could not read workbook: %s" % e)
    names = list(wb.sheetnames)
    if inspect:
        print(json.dumps({"ok": True, "sheets": names, "multi_sheet": len(names) > 1,
                          "prompt": ("A multi-sheet workbook cannot be carried into Git as one "
                                     "file. Offer: one CSV per sheet, or skip.")
                          if len(names) > 1 else None}, indent=2))
        return 0
    if len(names) > 1 and sheets not in ("all", "first"):
        return fail("multi-sheet workbook: ask the user to split into one CSV per sheet, or "
                    "skip. Never export one sheet and drop the rest.", sheets=names)
    targets = names if (sheets == "all") else names[:1]
    written, blanks = [], 0
    base = os.path.splitext(os.path.basename(rel))[0]
    for name in targets:
        ws = wb[name]
        suffix = "" if len(targets) == 1 else "." + "".join(
            c if c.isalnum() or c in "-_" else "_" for c in name)
        out = os.path.join(stage, base + suffix + ".csv")
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
            for row in ws.iter_rows(values_only=True):
                cells = []
                for v in row:
                    if v is None:
                        cells.append("")
                    elif isinstance(v, str) and v.startswith("="):
                        blanks += 1        # a formula with no cached value
                        cells.append(v)
                    else:
                        cells.append(v)
                w.writerow(cells)
        if os.path.getsize(out) > MB:
            return fail("exported CSV for sheet %r exceeds 1 MB; migration cannot complete"
                        % name, sheet=name, bytes=os.path.getsize(out))
        written.append({"sheet": name, "staged": out, "bytes": os.path.getsize(out),
                        "output": os.path.join(os.path.dirname(rel),
                                               os.path.basename(out)).lstrip("/"),
                        "output_hash": sha(out)})
    if blanks:
        return fail("workbook has %d formula cells with no cached value; a missing cache must "
                    "not become a blank cell. Report that conversion cannot complete." % blanks)
    print(json.dumps({"ok": True, "kind": "excel", "src": rel, "source_hash": sha(src_abs),
                      "source_size": os.path.getsize(src_abs), "sheets": names,
                      "outputs": written,
                      "reminder": "publishing the CSV and archiving the workbook are ONE "
                                  "migration; declining the archive move publishes nothing"},
                     indent=2))
    return 0


def ok(out, src_abs, rel, kind, **extra):
    d = {"ok": True, "kind": kind, "src": rel,
         "source_hash": sha(src_abs), "source_size": os.path.getsize(src_abs)}
    if out:
        d.update({"staged": out, "output": policy.output_name(rel, kind),
                  "output_hash": sha(out), "output_bytes": os.path.getsize(out)})
    d.update(extra)
    print(json.dumps(d, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", required=True,
                    choices=["image", "pdf", "word", "excel"])
    ap.add_argument("--src", required=True, help="path relative to --root")
    ap.add_argument("--root", required=True)
    ap.add_argument("--stage", help="staging directory (created if needed)")
    ap.add_argument("--sheets", choices=["first", "all"])
    ap.add_argument("--inspect", action="store_true")
    a = ap.parse_args()

    root = os.path.abspath(a.root)
    src_abs = os.path.join(root, a.src)
    if not os.path.isfile(src_abs):
        return fail("source not found: %s" % a.src)
    stage = a.stage
    if not a.inspect:
        if not stage:
            return fail("--stage is required")
        os.makedirs(stage, exist_ok=True)

    if a.kind == "image":
        return do_image(src_abs, stage, a.src)
    if a.kind == "pdf":
        return do_pdf(src_abs, stage, a.src)
    if a.kind == "word":
        return do_word(src_abs, stage, a.src)
    return do_excel(src_abs, stage, a.src, a.sheets, a.inspect)


if __name__ == "__main__":
    sys.exit(main())
