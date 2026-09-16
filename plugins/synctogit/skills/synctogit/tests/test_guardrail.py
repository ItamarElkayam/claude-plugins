"""Guardrail tests: nothing the skill invents may touch user data (GUARD-*)."""
import os
from harness import eq, is_in, sha


def guard01_collaborator_deletion_syncs(s):
    """Alice deletes and commits; Bob's clean copy is removed by ordinary sync."""
    alice = s.repo("guard01")
    alice.write("shared.md", "content\n")
    alice.commit_all("add shared")
    alice.git("push", "-q")
    bob = alice.clone("guard01-bob")
    assert os.path.exists(bob.path("shared.md"))

    alice.git("rm", "-q", "shared.md")
    alice.git("commit", "-qm", "remove shared")
    alice.git("push", "-q")

    rep, rc = bob.apply({"approved_paths": [], "message": "sync"}, ["--no-push"])
    eq(rc, 0, "an incoming deletion is ordinary sync, not a failure")
    eq(rep["integration"], "clean")
    assert not os.path.exists(bob.path("shared.md")), \
        "a user-authored deletion must propagate to a clean copy"
    assert not rep["refused"], "must not be refused as a forbidden deletion: %r" % rep["refused"]


def guard02_collaborator_edit_arrives(s):
    alice = s.repo("guard02")
    alice.write("paper.md", "v1\n")
    alice.commit_all("v1")
    alice.git("push", "-q")
    bob = alice.clone("guard02-bob")
    alice.write("paper.md", "v2\n")
    alice.commit_all("v2")
    alice.git("push", "-q")
    rep, rc = bob.apply({"approved_paths": []}, ["--no-push"])
    eq(rc, 0)
    eq(open(bob.path("paper.md")).read(), "v2\n")


def guard03_remote_deletion_vs_local_edits(s):
    alice = s.repo("guard03")
    alice.write("data.md", "original\n")
    alice.commit_all("add")
    alice.git("push", "-q")
    bob = alice.clone("guard03-bob")
    alice.git("rm", "-q", "data.md")
    alice.git("commit", "-qm", "delete")
    alice.git("push", "-q")

    bob.write("data.md", "my important local edits\n")
    rep, rc = bob.apply({"approved_paths": ["data.md"], "message": "keep my edits"},
                        ["--no-push"])
    eq(rc, 3, "a delete-vs-edit conflict must stop and ask")
    is_in("data.md", rep["conflicts"])
    eq(open(bob.path("data.md")).read(), "my important local edits\n",
       "local work must survive the conflict")
    is_in("nothing discarded", rep["integration"])


def guard04_excluded_file_survives_everything(s):
    r = s.repo("guard04")
    r.size_file("reads.fastq", 300_000)
    r.size_file("big.csv", 2_000_000)
    r.write("notes.md", "note\n")
    h_fastq, h_csv = sha(r.path("reads.fastq")), sha(r.path("big.csv"))

    r.apply({"approved_paths": ["notes.md"], "message": "notes"}, ["--no-push"])
    r.apply({"approved_paths": ["notes.md"], "message": "retry"}, ["--no-push"])
    r.apply({"approved_paths": [], "message": "cancelled"}, ["--no-push"])
    eq(sha(r.path("reads.fastq")), h_fastq, "excluded fastq must be byte-identical")
    eq(sha(r.path("big.csv")), h_csv, "oversized csv must be byte-identical")


def guard05_conversion_never_touches_source(s):
    r = s.repo("guard05")
    make_tiff(r, "figures/fig.tiff", 900, 700)
    before = sha(r.path("figures/fig.tiff"))
    stage = os.path.join(s.base, "stage05")
    out, rc = r.convert("image", "figures/fig.tiff", stage)
    assert out.get("ok"), out
    plan = {"approved_paths": [], "message": "add preview",
            "outputs": [{"src": "figures/fig.tiff", "staged": out["staged"],
                         "output": out["output"], "source_hash": out["source_hash"],
                         "source_size": out["source_size"]}]}
    rep, rc = r.apply(plan, ["--no-push"])
    eq(rc, 0, rep)
    eq(sha(r.path("figures/fig.tiff")), before, "source must be untouched by conversion")
    assert os.path.exists(r.path(out["output"])), "output should exist"


def guard06_archive_without_consent_refused(s):
    r = s.repo("guard06")
    r.write("data/book.xlsx", b"\x50\x4b\x03\x04fake workbook")
    before = sha(r.path("data/book.xlsx"))
    plan = {"approved_paths": [], "message": "migrate",
            "archive_moves": [{"src": "data/book.xlsx", "dest": "_archive/book.xlsx",
                               "consent": False}]}
    rep, rc = r.apply(plan, ["--no-push"])
    eq(rc, 2, "no consent means the whole apply is refused")
    is_in("no specific consent", " ".join(rep["refused"]))
    assert os.path.exists(r.path("data/book.xlsx")), "original must stay at its path"
    eq(sha(r.path("data/book.xlsx")), before)
    assert not os.path.exists(r.path("_archive/book.xlsx")), "nothing may be written"


def guard07_approved_archive_preserves_contents(s):
    r = s.repo("guard07")
    payload = b"workbook bytes \x00\x01" * 500
    r.write("data/book.xlsx", payload)
    before = sha(r.path("data/book.xlsx"))
    r.write("data/book.csv", "a,b\n1,2\n")
    plan = {"approved_paths": ["data/book.csv"], "message": "migrate workbook to CSV",
            "archive_moves": [{"src": "data/book.xlsx", "dest": "_archive/book.xlsx",
                               "consent": True}]}
    rep, rc = r.apply(plan, ["--no-push"])
    eq(rc, 0, rep)
    eq(sha(r.path("_archive/book.xlsx")), before, "archived copy must be byte-identical")
    assert not os.path.exists(r.path("data/book.xlsx")), "approved move relocates the source"
    eq(rep["archived"], [{"src": "data/book.xlsx", "dest": "_archive/book.xlsx"}])


def guard08_cleanup_instruction_refused(s):
    r = s.repo("guard08")
    r.write("keep.md", "keep\n")
    r.commit_all("keep")
    for key in ("delete", "cleanup", "remove", "deletions"):
        plan = {"approved_paths": [], key: ["keep.md"], "message": "tidy"}
        rep, rc = r.apply(plan, ["--no-push"])
        eq(rc, 2, "an unauthorized %s instruction must be refused" % key)
        is_in("unauthorized", " ".join(rep["refused"]))
        assert os.path.exists(r.path("keep.md")), "file must survive a refused cleanup"


def guard09_archive_and_outputs_not_deletable(s):
    r = s.repo("guard09")
    r.write("_archive/old.xlsx", b"preserved")
    r.write("fig.preview.jpg", b"user edited output")
    h1, h2 = sha(r.path("_archive/old.xlsx")), sha(r.path("fig.preview.jpg"))
    r.apply({"approved_paths": [], "message": "x"}, ["--no-push"])
    r.apply({"approved_paths": [], "cleanup": ["_archive/old.xlsx"]}, ["--no-push"])
    eq(sha(r.path("_archive/old.xlsx")), h1, "_archive must never be cleaned")
    eq(sha(r.path("fig.preview.jpg")), h2, "user-edited output must never be cleaned")


def guard10_missing_source_is_not_a_deletion(s):
    """A path that vanished from a scan must never become an outgoing deletion."""
    r = s.repo("guard10")
    r.write("keep.md", "content\n")
    r.write("gone.md", "content\n")
    r.commit_all("two files")
    # simulate a read failure rather than a user deletion
    os.chmod(r.path("gone.md"), 0o000)
    try:
        sc = r.scan()
        it = r.item(sc, "gone.md")
        assert it is None or it["decision"] != "upload" or it["category"] != "deletion", \
            "an unreadable file must never be reported as a user deletion: %r" % it
    finally:
        os.chmod(r.path("gone.md"), 0o644)
    assert "gone.md" in r.head_files(), "file must remain in Git"


def guard11_archive_destination_guards(s):
    r = s.repo("guard11")
    r.write("book.xlsx", b"data")
    for dest in ("../outside.xlsx", ".git/sneaky.xlsx"):
        rep, rc = r.apply({"approved_paths": [],
                           "archive_moves": [{"src": "book.xlsx", "dest": dest,
                                              "consent": True}]}, ["--no-push"])
        eq(rc, 2, "destination %s must be refused" % dest)
        assert os.path.exists(r.path("book.xlsx")), "source stays put"
    r.write("_archive/book.xlsx", b"already here")
    rep, rc = r.apply({"approved_paths": [],
                       "archive_moves": [{"src": "book.xlsx", "dest": "_archive/book.xlsx",
                                          "consent": True}]}, ["--no-push"])
    eq(rc, 2, "must not overwrite an existing archive entry")
    eq(open(r.path("_archive/book.xlsx"), "rb").read(), b"already here")


def make_tiff(repo, rel, w, h):
    from PIL import Image
    import random
    p = repo.path(rel)
    os.makedirs(os.path.dirname(p) or repo.dir, exist_ok=True)
    im = Image.new("RGB", (w, h))
    random.seed(w * h)
    im.putdata([(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
                for _ in range(w * h)])
    im.save(p, "TIFF")
    return p


TESTS = [
    ("GUARD-01 collaborator deletion syncs", guard01_collaborator_deletion_syncs),
    ("GUARD-02 collaborator edit arrives", guard02_collaborator_edit_arrives),
    ("GUARD-03 remote delete vs local edit", guard03_remote_deletion_vs_local_edits),
    ("GUARD-04 excluded data survives all paths", guard04_excluded_file_survives_everything),
    ("GUARD-05 conversion never touches source", guard05_conversion_never_touches_source),
    ("GUARD-06 archive without consent refused", guard06_archive_without_consent_refused),
    ("GUARD-07 approved archive preserves bytes", guard07_approved_archive_preserves_contents),
    ("GUARD-08 cleanup instruction refused", guard08_cleanup_instruction_refused),
    ("GUARD-09 archive/outputs not deletable", guard09_archive_and_outputs_not_deletable),
    ("GUARD-10 missing source is not a deletion", guard10_missing_source_is_not_a_deletion),
    ("GUARD-11 archive destination guards", guard11_archive_destination_guards),
]
