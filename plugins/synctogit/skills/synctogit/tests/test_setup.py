"""Setup, root-refusal, and converter-degradation tests (SETUP-*, REPO-02)."""
import json, os, subprocess, sys
from harness import eq, is_in, SCRIPTS


def repo02_refused_roots(s):
    home = os.path.expanduser("~")
    for cand in (home, os.path.join(home, "Desktop"), os.path.join(home, "Downloads"),
                 os.path.join(home, "Library", "Mobile Documents")):
        p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "scan.py"), "--root", cand],
                           capture_output=True, text=True)
        out = json.loads(p.stdout)
        eq(out.get("refused_root"), True, "%s must be refused as a project root" % cand)
    r = s.repo("ordinary")
    eq(r.scan().get("refused_root"), None, "an ordinary project folder is fine")


def setup01_reports_missing_identity(s):
    r = s.repo("setup01")
    r.git("config", "--unset", "user.email")
    p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "setup_check.py"),
                        "--root", r.dir], capture_output=True, text=True,
                       env=dict(os.environ, HOME=s.base, GIT_CONFIG_GLOBAL=os.path.join(
                           s.base, "no-such-gitconfig")))
    out = json.loads(p.stdout)
    joined = " ".join(out["ask_user"])
    is_in("user.email", joined, "a missing git identity must be surfaced before commit time")
    eq(out["ready"], False)


def setup02_missing_converter_degrades(s):
    """A converter that is absent must exclude its file type, not crash the run."""
    r = s.repo("setup02")
    r.write("doc.docx", b"PK\x03\x04not really a docx")
    out, rc = r.convert("word", "doc.docx", os.path.join(s.base, "st-setup02"))
    if out.get("ok"):
        return                                  # pandoc present: nothing to prove here
    eq(rc, 1)
    is_in("Pandoc", out["error"])
    assert os.path.exists(r.path("doc.docx")), "the source must survive a failed conversion"


def setup03_gitattributes_created_once(s):
    r = s.repo("setup03")
    r.write("a.md", "x\n")
    rep, rc = r.apply({"approved_paths": ["a.md"], "gitattributes": True, "message": "a"},
                      ["--no-push"])
    eq(rc, 0, rep)
    eq(open(r.path(".gitattributes")).read(), "* text=auto\n")
    is_in(".gitattributes", r.head_files())
    open(r.path(".gitattributes"), "a").write("*.csv text\n")   # a user customises it
    r.write("b.md", "y\n")
    r.apply({"approved_paths": ["b.md"], "gitattributes": True, "message": "b"}, ["--no-push"])
    is_in("*.csv text", open(r.path(".gitattributes")).read(),
          "an existing .gitattributes must never be overwritten")


def setup04_ignore_template(s):
    """One static template at setup; never rewritten, never per-file entries."""
    r = s.repo("setup04")
    r.write("a.md", "x\n")
    rep, rc = r.apply({"approved_paths": ["a.md"], "gitignore_template": True,
                       "message": "a"}, ["--no-push"])
    eq(rc, 0, rep)
    txt = open(r.path(".gitignore")).read()
    for t in ("*.fastq", "*.tiff", "*.pptx", "_archive/"):
        is_in(t, txt, "the template must cover %s" % t)
    is_in(".gitignore", r.head_files())

    open(r.path(".gitignore"), "a").write("my_own_rule/\n")
    r.write("b.md", "y\n")
    r.apply({"approved_paths": ["b.md"], "gitignore_template": True, "message": "b"},
            ["--no-push"])
    txt = open(r.path(".gitignore")).read()
    is_in("my_own_rule/", txt, "an existing .gitignore is never rewritten")
    eq(txt.splitlines().count("*.fastq"), 1, "and never duplicated")


def excel01_multi_sheet_asks(s):
    r = s.repo("excel01")
    try:
        import openpyxl
    except ImportError:
        return
    wb = openpyxl.Workbook()
    wb.active.title = "measurements"
    wb.active.append(["id", "value"])
    wb.active.append(["0012", 1.5])
    wb.create_sheet("metadata").append(["note", "second sheet"])
    wb.save(r.path("book.xlsx"))
    out, rc = r.convert("excel", "book.xlsx", os.path.join(s.base, "st-x1"))
    eq(rc, 1, "a multi-sheet workbook must not convert silently")
    is_in("multi-sheet", out["error"])
    eq(sorted(out["sheets"]), ["measurements", "metadata"])
    out, rc = r.convert("excel", "book.xlsx", os.path.join(s.base, "st-x2"),
                        ["--sheets", "all"])
    eq(rc, 0, out)
    eq(len(out["outputs"]), 2, "splitting must produce one CSV per sheet")
    text = open(out["outputs"][0]["staged"]).read()
    is_in("0012", text, "textual identifiers must keep their leading zeros")


TESTS = [
    ("REPO-02 refused project roots", repo02_refused_roots),
    ("SETUP-01 missing git identity", setup01_reports_missing_identity),
    ("SETUP-02 missing converter degrades", setup02_missing_converter_degrades),
    ("SETUP-03 gitattributes once, never clobbered", setup03_gitattributes_created_once),
    ("SETUP-04 ignore template written once", setup04_ignore_template),
    ("CSV-02 multi-sheet workbook asks", excel01_multi_sheet_asks),
]
