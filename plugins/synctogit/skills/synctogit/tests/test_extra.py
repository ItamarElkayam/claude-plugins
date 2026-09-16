"""Second wave: migration coupling, locking, undo, nested repos, cloud stubs, renames."""
import json, os, subprocess, sys, time
from harness import eq, is_in, sha, SCRIPTS, Repo


def doc02_migration_coupling_enforced(s):
    """Publishing a converted document without its approved archive move is refused."""
    r = s.repo("doc02")
    r.write("report.docx", b"PK\x03\x04pretend docx")
    stage = os.path.join(s.base, "st-doc02")
    os.makedirs(stage, exist_ok=True)
    md = os.path.join(stage, "report.md")
    open(md, "w").write("# report\n")
    before = sha(r.path("report.docx"))
    out = {"src": "report.docx", "staged": md, "output": "report.md",
           "source_hash": before, "source_size": os.path.getsize(r.path("report.docx")),
           "requires_archive_consent": True}

    rep, rc = r.apply({"approved_paths": [], "outputs": [out], "message": "migrate"},
                      ["--no-push"])
    eq(rc, 2, "no archive consent means the migration publishes nothing")
    is_in("stays incomplete", " ".join(rep["refused"]))
    assert not os.path.exists(r.path("report.md")), "no .md may be published"
    assert os.path.exists(r.path("report.docx")), "the .docx stays where it is"
    eq(sha(r.path("report.docx")), before)

    rep, rc = r.apply({"approved_paths": [], "outputs": [out], "message": "migrate",
                       "archive_moves": [{"src": "report.docx", "dest": "_archive/report.docx",
                                          "consent": True}]}, ["--no-push"])
    eq(rc, 0, rep)
    assert os.path.exists(r.path("report.md")), "with consent, the .md is published"
    eq(sha(r.path("_archive/report.docx")), before, "and the original is preserved verbatim")
    assert not os.path.exists(r.path("report.docx"))
    assert "report.md" in r.head_files()
    assert not any(f.startswith("_archive/") for f in r.head_files()), \
        "_archive must never be uploaded"


def csv01_one_active_table(s):
    r = s.repo("csv01")
    try:
        import openpyxl
    except ImportError:
        return
    wb = openpyxl.Workbook()
    wb.active.append(["id", "value"])
    wb.active.append(["0042", 3.14159265358979])
    wb.save(r.path("table.xlsx"))
    before = sha(r.path("table.xlsx"))
    out, rc = r.convert("excel", "table.xlsx", os.path.join(s.base, "st-csv01"))
    eq(rc, 0, out)
    o = out["outputs"][0]
    rep, rc = r.apply({"approved_paths": [], "message": "migrate table",
                       "outputs": [{"src": "table.xlsx", "staged": o["staged"],
                                    "output": o["output"], "source_hash": out["source_hash"],
                                    "source_size": out["source_size"],
                                    "requires_archive_consent": True}],
                       "archive_moves": [{"src": "table.xlsx", "dest": "_archive/table.xlsx",
                                          "consent": True}]}, ["--no-push"])
    eq(rc, 0, rep)
    eq(sha(r.path("_archive/table.xlsx")), before)
    assert os.path.exists(r.path("table.csv")), "one active CSV remains"
    assert not os.path.exists(r.path("table.xlsx")), "no second active format beside it"
    text = open(r.path("table.csv")).read()
    is_in("0042", text, "identifiers keep leading zeros")
    is_in("3.14159265358979", text, "numeric precision preserved")


def undo01_revert_commit(s):
    r = s.repo("undo01")
    r.write("note.md", "first\n")
    r.apply({"approved_paths": ["note.md"], "message": "add note"}, [])
    assert os.path.exists(r.path("note.md"))
    p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "undo.py"), "--root", r.dir],
                       capture_output=True, text=True)
    rep = json.loads(p.stdout)
    eq(rep["ok"], True, rep)
    assert not os.path.exists(r.path("note.md")), "the sync is reversed"
    assert len(r.log()) >= 3, "reversal is a new commit, not a rewrite"
    is_in("Revert", r.git("log", "-1", "--format=%s").stdout)
    eq(rep.get("pushed"), True)


def undo02_describe_changes_nothing(s):
    r = s.repo("undo02")
    r.write("note.md", "first\n")
    r.apply({"approved_paths": ["note.md"], "message": "add note"}, ["--no-push"])
    before = r.log()
    p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "undo.py"), "--root", r.dir,
                        "--describe"], capture_output=True, text=True)
    rep = json.loads(p.stdout)
    eq(r.log(), before, "--describe must change nothing")
    is_in("note.md", " ".join(rep["files"]))


def undo03_conflicting_revert_preserved(s):
    r = s.repo("undo03")
    r.write("note.md", "first\n")
    r.apply({"approved_paths": ["note.md"], "message": "add note"}, ["--no-push"])
    r.write("note.md", "first\nsecond line by someone else\n")
    r.commit_all("later work on the same file")
    p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "undo.py"), "--root", r.dir,
                        "--commit", "HEAD~1", "--no-push"], capture_output=True, text=True)
    rep = json.loads(p.stdout)
    eq(rep["ok"], False, "a conflicting revert must stop, not force")
    is_in("nothing was discarded", rep["note"])
    r.git("revert", "--abort", check=False)
    is_in("second line by someone else", open(r.path("note.md")).read())


def nested01_nested_repo_not_recursed(s):
    r = s.repo("nested01")
    inner = os.path.join(r.dir, "subproject")
    os.makedirs(inner)
    subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q", inner], check=True)
    open(os.path.join(inner, "inner.md"), "w").write("inner content\n")
    open(os.path.join(inner, "reads.fastq"), "w").write("x")
    sc = r.scan()
    paths = [i["path"] for i in sc["items"]]
    inside = [p for p in paths if p.startswith("subproject/") and p != "subproject/"]
    assert not inside, "a nested repository's files must never be listed: %r" % inside
    it = r.item(sc, "subproject/")
    assert it is not None, "the nested repository itself must be reported, not silently hidden"
    eq(it["category"], "nested-repo")
    eq(it["decision"], "exclude")
    is_in("not recursed", it["reason"])


def cloud01_placeholder_detected_only_in_cloud_trees(s):
    """A dataless stub inside a cloud tree is reported; an ordinary sparse file is not."""
    cloud_base = os.path.join(s.base, "Dropbox")
    os.makedirs(cloud_base, exist_ok=True)
    r = Repo(cloud_base, "cloudproj", with_remote=False)
    with open(r.path("not-downloaded.md"), "wb") as f:
        f.truncate(1_000_000)
    sc = r.scan()
    it = r.item(sc, "not-downloaded.md")
    eq(it["decision"], "exclude")
    eq(it["reason"], "cloud file not present locally")

    plain = s.repo("sparse-ok", with_remote=False)
    with open(plain.path("sparse.md"), "wb") as f:
        f.truncate(1_000_000)
    sc = plain.scan()
    eq(plain.item(sc, "sparse.md")["decision"], "upload",
       "an ordinary sparse file outside a cloud tree is a normal file")


def rename01_rename_reported_once(s):
    r = s.repo("rename01")
    r.write("old.md", "content\n")
    r.commit_all("add old")
    r.git("mv", "old.md", "new.md")
    sc = r.scan()
    paths = [i["path"] for i in sc["items"]]
    is_in("new.md", paths, "the new path must be what is reviewed")
    eq(r.item(sc, "new.md")["decision"], "upload")


def git05a_git_refuses_to_clobber_untracked(s):
    """The common case: git itself stops rather than overwrite an untracked local file."""
    alice = s.repo("git05a")
    alice.write("a.md", "a\n")
    alice.commit_all("base")
    alice.git("push", "-q")
    bob = alice.clone("git05a-bob")
    # Alice adds a new tracked file; Bob already has his own untracked file at that path
    alice.write("shared.md", "alice's version\n")
    alice.commit_all("add shared")
    alice.git("push", "-q")
    bob.write("shared.md", "bob's own local data\n")

    rep, rc = bob.apply({"approved_paths": [], "message": "sync"}, ["--no-push"])
    eq(open(bob.path("shared.md")).read(), "bob's own local data\n",
       "git must not overwrite an untracked local file during a pull")
    eq(rc, 3, "the aborted pull is reported as a conflict to resolve with the user")


def git05b_modify_delete_is_gits_behaviour(s):
    """Documented limitation: we take git's resolution, which overwrites the local file.

    Reaching this needs a manual `git rm --cached` — the skill never untracks anything. The
    test exists so the behaviour is recorded, not so it is relied upon.
    """
    alice = s.repo("git05b")
    alice.write("shared.md", "alice content\n")
    alice.commit_all("add shared")
    alice.git("push", "-q")
    bob = alice.clone("git05b-bob")
    bob.git("rm", "-q", "--cached", "shared.md")
    bob.write(".gitignore", "shared.md\n")
    bob.git("add", ".gitignore")
    bob.git("commit", "-qm", "stop tracking locally")
    bob.write("shared.md", "bob's own local data\n")
    alice.write("shared.md", "alice update\n")
    alice.commit_all("update")
    alice.git("push", "-q")

    rep, rc = bob.apply({"approved_paths": [], "message": "sync"}, ["--no-push"])
    eq(rc, 3, "git reports a modify/delete conflict, and we surface it")
    is_in("shared.md", rep["conflicts"])
    eq(open(bob.path("shared.md")).read(), "alice update\n",
       "git's own resolution puts the incoming version in the tree; we do not second-guess it")
    is_in("nothing discarded", rep["integration"])


def deletion01_user_deletion_propagates(s):
    r = s.repo("deletion01")
    r.write("gone.md", "content\n")
    r.write("stays.md", "content\n")
    r.commit_all("two files")
    r.git("push", "-q")
    os.remove(r.path("gone.md"))
    sc = r.scan()
    it = r.item(sc, "gone.md")
    eq(it["category"], "deletion")
    eq(it["decision"], "upload", "a deletion the user made is an ordinary change")
    eq(it["origin"], "user_change")
    rep, rc = r.apply({"approved_paths": ["gone.md"], "message": "remove gone.md"}, [])
    eq(rc, 0, rep)
    assert "gone.md" not in r.head_files(), "the user's deletion is committed"
    assert "stays.md" in r.head_files()
    eq(rep["pushed"], True)


def push02_remote_advanced_then_pushes(s):
    alice = s.repo("push02")
    alice.write("a.md", "a\n")
    alice.commit_all("a")
    alice.git("push", "-q")
    bob = alice.clone("push02-bob")
    alice.write("b.md", "b\n")
    alice.commit_all("b")
    alice.git("push", "-q")
    bob.write("c.md", "c\n")
    rep, rc = bob.apply({"approved_paths": ["c.md"], "message": "add c"}, [])
    eq(rc, 0, rep)
    eq(rep["integration"], "clean")
    eq(rep["pushed"], True)
    assert os.path.exists(bob.path("b.md")), "collaborator work arrives in the same run"


def report01_every_exclusion_has_a_reason(s):
    r = s.repo("report01")
    r.size_file("reads.fastq", 100)
    r.size_file("talk.pptx", 100)
    r.size_file("big.csv", 1_500_000)
    r.size_file("v.mp4", 100)
    r.write("ok.md", "fine\n")
    sc = r.scan()
    for i in sc["items"]:
        if i["decision"] == "exclude":
            assert i.get("reason"), "every exclusion needs a reason: %r" % i
    eq(r.item(sc, "ok.md")["decision"], "upload")
    eq(sc["counts"].get("exclude"), 4)


TESTS = [
    ("DOC-02 migration coupling enforced", doc02_migration_coupling_enforced),
    ("CSV-01 one active table after migration", csv01_one_active_table),
    ("UNDO-01 revert commit", undo01_revert_commit),
    ("UNDO-02 describe changes nothing", undo02_describe_changes_nothing),
    ("UNDO-03 conflicting revert preserved", undo03_conflicting_revert_preserved),
    ("NESTED-01 nested repo not recursed", nested01_nested_repo_not_recursed),
    ("CLOUD-01 placeholder only in cloud trees", cloud01_placeholder_detected_only_in_cloud_trees),
    ("RENAME-01 rename reported once", rename01_rename_reported_once),
    ("GIT-05a git refuses to clobber untracked", git05a_git_refuses_to_clobber_untracked),
    ("GIT-05b modify/delete is git's call", git05b_modify_delete_is_gits_behaviour),
    ("DEL-01 user deletion propagates", deletion01_user_deletion_propagates),
    ("PUSH-02 remote advanced, then pushes", push02_remote_advanced_then_pushes),
    ("REPORT-01 every exclusion has a reason", report01_every_exclusion_has_a_reason),
]
