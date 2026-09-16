"""File-policy and reporting tests (FILE-*, LINK-*, GIT-01)."""
import os
from harness import eq, is_in


def file01_csv_boundaries(s):
    r = s.repo("csv-bounds")
    r.size_file("a999999.csv", 999_999)
    r.size_file("b1000000.csv", 1_000_000)
    r.size_file("c1000001.csv", 1_000_001)
    sc = r.scan()
    eq(r.item(sc, "a999999.csv")["decision"], "upload", "999,999 bytes")
    eq(r.item(sc, "b1000000.csv")["decision"], "upload", "exactly 1,000,000 bytes")
    eq(r.item(sc, "c1000001.csv")["decision"], "exclude", "1,000,001 bytes")
    eq(r.item(sc, "c1000001.csv")["reason"], "CSV exceeds 1 MB")


def file02_experimental_case_and_gz(s):
    r = s.repo("experimental")
    for n in ("reads.FASTQ.GZ", "reads2.fastq", "aln.BAM", "sig.pod5", "ms.mzML",
              "img.nd2", "cyt.fcs"):
        r.size_file(n, 50)
    sc = r.scan()
    for n in ("reads.FASTQ.GZ", "reads2.fastq", "aln.BAM", "sig.pod5", "ms.mzML",
              "img.nd2", "cyt.fcs"):
        it = r.item(sc, n)
        eq(it["decision"], "exclude", n)
        eq(it["reason"], "raw experimental format", n)


def file03_tiny_binaries(s):
    r = s.repo("tiny-bin")
    for n in ("d.zip", "v.mp4", "arr.npy", "m.mat", "t.pt"):
        r.size_file(n, 12)
    sc = r.scan()
    for n in ("d.zip", "v.mp4", "arr.npy", "m.mat", "t.pt"):
        eq(r.item(sc, n)["decision"], "exclude", n + " (tiny but unhandled)")


def file04_text_uncapped(s):
    r = s.repo("text-uncapped")
    r.write("notes.md", "# big\n" + ("word " * 500_000))
    r.write("analysis.ipynb", '{"cells":[' + ','.join(['{"cell_type":"code"}'] * 20_000) + ']}')
    r.write("script.py", "print(1)\n")
    r.write("cfg.json", '{"a":1}')
    sc = r.scan()
    for n in ("notes.md", "analysis.ipynb", "script.py", "cfg.json"):
        it = r.item(sc, n)
        eq(it["decision"], "upload", n)
    assert os.path.getsize(r.path("notes.md")) > 2_000_000, "test fixture should exceed 2 MB"


def file05_powerpoint_no_server_reminder(s):
    r = s.repo("pptx")
    r.size_file("talk.pptx", 40_000_000)
    r.size_file("reads.fastq", 100)
    sc = r.scan()
    ppt = r.item(sc, "talk.pptx")
    eq(ppt["decision"], "exclude")
    eq(ppt["reason"], "PowerPoint: managed locally only")
    eq(ppt["category"], "powerpoint", "must be its own category, not the server-reminder group")
    eq(r.item(sc, "reads.fastq")["category"], "experimental")


def link01_symlink_outside_project(s):
    r = s.repo("symlink")
    outside = os.path.join(s.base, "elsewhere")
    os.makedirs(outside, exist_ok=True)
    open(os.path.join(outside, "secret.md"), "w").write("data\n")
    os.symlink(outside, r.path("results"))
    sc = r.scan()
    it = r.item(sc, "results")
    assert it is not None, "symlink must be reported, not silently dropped"
    eq(it["decision"], "exclude")
    eq(it["reason"], "symlink pointing outside the project")
    assert os.path.exists(os.path.join(outside, "secret.md")), "target must be untouched"
    assert os.path.islink(r.path("results")), "symlink itself must be untouched"


def link02_unreadable_file(s):
    r = s.repo("unreadable")
    p = r.size_file("locked.md", 100)
    os.chmod(p, 0o000)
    try:
        sc = r.scan()
        it = r.item(sc, "locked.md")
        eq(it["decision"], "exclude")
        is_in("could not be read", it["reason"])
    finally:
        os.chmod(p, 0o644)


def git01_tracked_csv_grows(s):
    r = s.repo("grown-csv")
    r.size_file("table.csv", 500)
    r.write("notes.md", "hello\n")
    r.commit_all("add table and notes")
    r.size_file("table.csv", 1_400_000)
    r.write("notes.md", "hello again\n")
    before = open(r.path("table.csv"), "rb").read()

    sc = r.scan()
    csv = r.item(sc, "table.csv")
    eq(csv["decision"], "exclude", "oversized tracked CSV")
    is_in("already tracked", csv.get("note", ""), "must warn against untracking")

    rep, rc = r.apply({"approved_paths": ["notes.md"], "message": "update notes"},
                      ["--no-push"])
    eq(rc, 0)
    eq(open(r.path("table.csv"), "rb").read(), before, "local CSV contents preserved")
    assert "table.csv" in r.head_files(), "previous committed version must remain in Git"
    committed = r.git("show", "--name-only", "--format=", "HEAD").stdout.split()
    eq(committed, ["notes.md"], "only the approved file is committed")
    eq(len(r.git("log", "--diff-filter=D", "--name-only", "--format=", "HEAD"
                 ).stdout.strip()), 0, "no deletion may be committed to untrack it")


def file06_unknown_extension_sniffed(s):
    r = s.repo("sniff")
    r.write("mystery.weird", "plain text content\n")
    r.write("blob.weird2", b"\x00\x01binary\x00")
    sc = r.scan()
    eq(r.item(sc, "mystery.weird")["decision"], "upload", "text-like unknown extension")
    eq(r.item(sc, "blob.weird2")["decision"], "exclude", "binary unknown extension")


def file07_ignored_files_reported_without_walking(s):
    r = s.repo("ignored")
    r.write(".gitignore", "fast5/\n*.fastq\n")
    os.makedirs(r.path("fast5"), exist_ok=True)
    for i in range(30):
        r.size_file("fast5/read%02d.fast5" % i, 1000)
    r.size_file("x.fastq", 100)
    sc = r.scan()
    entries = sc["ignored_summary"]["entries"]
    assert any(e.startswith("fast5") for e in entries), "ignored dir must be reported"
    assert not any(e.startswith("fast5/read") for e in entries), \
        "must collapse the directory rather than list 30 files: %r" % entries
    eq(sc["ignored_summary"]["reason"], "existing ignore rule")


TESTS = [
    ("FILE-01 csv boundaries", file01_csv_boundaries),
    ("FILE-02 experimental case/gz", file02_experimental_case_and_gz),
    ("FILE-03 tiny binaries excluded", file03_tiny_binaries),
    ("FILE-04 text and notebooks uncapped", file04_text_uncapped),
    ("FILE-05 powerpoint has own reason", file05_powerpoint_no_server_reminder),
    ("FILE-06 unknown extension sniffed", file06_unknown_extension_sniffed),
    ("FILE-07 ignored tree collapsed", file07_ignored_files_reported_without_walking),
    ("LINK-01 symlink outside project", link01_symlink_outside_project),
    ("LINK-02 unreadable file", link02_unreadable_file),
    ("GIT-01 tracked csv grows too large", git01_tracked_csv_grows),
]
