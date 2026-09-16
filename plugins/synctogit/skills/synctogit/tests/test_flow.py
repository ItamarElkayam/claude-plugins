"""Selection, manifest, git-safety and review-page tests (SELECT/MEM/IMAGE/GIT/STATE/REVIEW)."""
import json, os, subprocess, sys, time, urllib.request, urllib.error
from harness import eq, is_in, sha, SCRIPTS, review_server
from test_guardrail import make_tiff


# ---------- per-file selection -------------------------------------------
def select01_leave_one_out(s):
    r = s.repo("select01")
    for n in ("a.md", "b.md", "c.md", "d.md", "e.md"):
        r.write(n, "content of %s\n" % n)
    keep = ["a.md", "b.md", "c.md", "d.md"]
    rep, rc = r.apply({"approved_paths": keep, "message": "add four notes"}, ["--no-push"])
    eq(rc, 0, rep)
    eq(sorted(r.head_files() - {"README.md"}), keep, "only the four approved files land")
    assert os.path.exists(r.path("e.md")), "the file left out must still exist locally"
    assert "e.md" not in r.tracked(), "and must remain untracked"
    sc = r.scan()
    eq(r.item(sc, "e.md")["decision"], "upload", "it is offered again on the next run")


def ux07_no_change_no_commit(s):
    r = s.repo("ux07")
    before = r.log()
    rep, rc = r.apply({"approved_paths": [], "message": "nothing"}, ["--no-push"])
    eq(rc, 0)
    eq(r.log(), before, "a no-change run must not create a commit")
    assert not rep["committed"]


# ---------- manifest / conversion memory ---------------------------------
def convert_and_apply(s, r, rel, stage_name):
    stage = os.path.join(s.base, stage_name)
    out, rc = r.convert("image", rel, stage)
    assert out.get("ok"), out
    plan = {"approved_paths": [], "message": "add preview",
            "outputs": [{"src": rel, "staged": out["staged"], "output": out["output"],
                         "source_hash": out["source_hash"],
                         "source_size": out["source_size"]}]}
    rep, rc = r.apply(plan, ["--no-push"])
    eq(rc, 0, rep)
    return out


def image01_output_matches_preview(s):
    r = s.repo("image01")
    make_tiff(r, "fig.tiff", 800, 600)
    out = convert_and_apply(s, r, "fig.tiff", "st-image01")
    eq(sha(r.path(out["output"])), out["output_hash"], "committed output == previewed bytes")
    assert os.path.getsize(r.path(out["output"])) <= 1_000_000, "output must be <= 1 MB"
    m = json.load(open(r.path(".synctogit.json")))
    is_in("fig.tiff", m["conversions"])
    assert ".synctogit.json" in r.head_files(), "manifest must be committed"


def image04_edited_source_reoffers(s):
    r = s.repo("image04")
    make_tiff(r, "fig.tiff", 800, 600)
    convert_and_apply(s, r, "fig.tiff", "st-image04")
    sc = r.scan()
    eq(r.item(sc, "fig.tiff")["manifest_state"], "unchanged")
    eq(r.item(sc, "fig.tiff")["decision"], "skip", "unchanged source asks nothing")

    make_tiff(r, "fig.tiff", 820, 640)          # the user adds labels / re-exports
    sc = r.scan()
    it = r.item(sc, "fig.tiff")
    eq(it["manifest_state"], "source_changed")
    eq(it["decision"], "convert", "an edited original must be offered again")
    is_in("original changed", it["reason"])


def image05_declined_then_edited_again(s):
    r = s.repo("image05")
    make_tiff(r, "fig.tiff", 700, 500)
    h1 = sha(r.path("fig.tiff"))
    rep, rc = r.apply({"approved_paths": [], "message": "record decline",
                       "declined": [{"src": "fig.tiff", "source_hash": h1}]}, ["--no-push"])
    eq(rc, 0, rep)
    sc = r.scan()
    it = r.item(sc, "fig.tiff")
    eq(it["manifest_state"], "declined")
    eq(it["decision"], "exclude")
    eq(it["reason"], "image conversion declined", "stays declined for this revision")

    make_tiff(r, "fig.tiff", 720, 520)          # edited again
    sc = r.scan()
    eq(r.item(sc, "fig.tiff")["decision"], "convert",
       "a new revision must be offered even though the old one was declined")


def image02_edited_output_left_alone(s):
    r = s.repo("image02")
    make_tiff(r, "fig.tiff", 800, 600)
    out = convert_and_apply(s, r, "fig.tiff", "st-image02")
    open(r.path(out["output"]), "ab").write(b"user's own annotation bytes")
    edited = sha(r.path(out["output"]))
    sc = r.scan()
    it = r.item(sc, "fig.tiff")
    eq(it["manifest_state"], "output_changed")
    eq(it["decision"], "skip")
    is_in("edited directly", it["reason"])
    eq(sha(r.path(out["output"])), edited, "the user's edited output is untouched")


def image06_both_changed_asks(s):
    r = s.repo("image06")
    make_tiff(r, "fig.tiff", 800, 600)
    out = convert_and_apply(s, r, "fig.tiff", "st-image06")
    open(r.path(out["output"]), "ab").write(b"annotation")
    make_tiff(r, "fig.tiff", 810, 610)
    sc = r.scan()
    it = r.item(sc, "fig.tiff")
    eq(it["manifest_state"], "both_changed")
    eq(it["decision"], "ask", "both edited must ask, never overwrite")


def mem02_missing_manifest_treats_all_as_new(s):
    r = s.repo("mem02")
    make_tiff(r, "fig.tiff", 700, 500)
    out = convert_and_apply(s, r, "fig.tiff", "st-mem02")
    outhash = sha(r.path(out["output"]))
    os.remove(r.path(".synctogit.json"))
    sc = r.scan()
    eq(sc["manifest_ok"], True, "an absent manifest is normal, not corrupt")
    eq(r.item(sc, "fig.tiff")["manifest_state"], "new")
    assert os.path.exists(r.path(out["output"])), "no output may be deleted"
    eq(sha(r.path(out["output"])), outhash)


def mem02b_corrupt_manifest(s):
    r = s.repo("mem02b")
    make_tiff(r, "fig.tiff", 700, 500)
    out = convert_and_apply(s, r, "fig.tiff", "st-mem02b")
    open(r.path(".synctogit.json"), "w").write("<<<<<<< HEAD\n{not json\n")
    sc = r.scan()
    eq(sc["manifest_ok"], False, "a conflicted manifest must be reported")
    eq(r.item(sc, "fig.tiff")["manifest_state"], "new")
    assert os.path.exists(r.path(out["output"]))


def mem03_deleted_output_offered_again(s):
    """Deleting a preview on purpose: it is not recreated, and the source is offered once more."""
    r = s.repo("mem03")
    make_tiff(r, "fig.tiff", 700, 500)
    out = convert_and_apply(s, r, "fig.tiff", "st-mem03")
    r.git("rm", "-q", out["output"])
    r.git("commit", "-qm", "remove the preview on purpose")
    sc = r.scan()
    it = r.item(sc, "fig.tiff")
    eq(it["manifest_state"], "new", "with its output gone, the source is simply new again")
    eq(it["decision"], "convert", "so it is offered once, and a decline is then remembered")
    assert not os.path.exists(r.path(out["output"])), "nothing may be recreated by a scan"


def mem01_manifest_travels_to_a_clone(s):
    r = s.repo("mem01")
    make_tiff(r, "fig.tiff", 700, 500)
    r.write(".gitignore", "*.tiff\n")
    r.git("add", ".gitignore")
    r.git("commit", "-qm", "ignore tiffs")
    out = convert_and_apply(s, r, "fig.tiff", "st-mem01")
    r.git("push", "-q")
    other = r.clone("mem01-other")
    assert os.path.exists(other.path(".synctogit.json")), "manifest reaches a fresh clone"
    sc = other.scan()
    it = other.item(sc, "fig.tiff")
    assert it is None, "a clone without the ignored original has nothing to offer"
    assert os.path.exists(other.path(out["output"])), \
        "an absent source is never a reason to delete the output"


# ---------- git safety ----------------------------------------------------
def git04_preserves_staged_work(s):
    r = s.repo("git04")
    r.write("mine.md", "work in progress\n")
    r.git("add", "mine.md")                      # user staged this themselves
    r.write("other.md", "unrelated\n")
    rep, rc = r.apply({"approved_paths": ["other.md"], "message": "unrelated change"},
                      ["--no-push"])
    eq(rc, 0, rep)
    staged = r.git("diff", "--name-only", "--cached").stdout.split()
    is_in("mine.md", staged, "pre-existing staged work must survive")
    eq(r.git("show", "--name-only", "--format=", "HEAD").stdout.split(), ["other.md"])
    is_in("mine.md", rep["preexisting_staged_kept"])


def git06_push_failure_reported(s):
    r = s.repo("git06")
    r.write("note.md", "hello\n")
    r.git("remote", "set-url", "origin", os.path.join(s.base, "does-not-exist.git"))
    rep, rc = r.apply({"approved_paths": ["note.md"], "message": "add note"},
                      ["--no-integrate"])
    eq(rc, 0)
    eq(rep["pushed"], False)
    eq(rep["status"], "Saved locally; upload incomplete")
    assert rep["commit"], "the local commit must be kept"
    eq(r.git("show", "--name-only", "--format=", "HEAD").stdout.split(), ["note.md"])


def git06b_retry_makes_no_duplicate(s):
    r = s.repo("git06b")
    r.write("note.md", "hello\n")
    good = r.git("remote", "get-url", "origin").stdout.strip()
    r.git("remote", "set-url", "origin", os.path.join(s.base, "nope.git"))
    r.apply({"approved_paths": ["note.md"], "message": "add note"}, ["--no-integrate"])
    n_before = len(r.log())
    r.git("remote", "set-url", "origin", good)
    rep, rc = r.apply({"approved_paths": ["note.md"], "message": "add note"}, [])
    eq(rc, 0, rep)
    eq(len(r.log()), n_before, "a retry must not create a duplicate commit")
    eq(rep["pushed"], True)


def git02_disallowed_blob_in_unpushed_history(s):
    r = s.repo("git02")
    r.size_file("sneaky.bam", 5000)
    r.git("add", "-f", "sneaky.bam")
    r.git("commit", "-qm", "oops, committed a bam outside the skill")
    sc = r.scan()
    eq(sc["unpushed_commits"], 1)
    paths = [x["path"] for x in sc["unpushed_disallowed"]]
    is_in("sneaky.bam", paths, "a disallowed blob in unpushed history must be detected")


def state01_current_content_is_what_syncs(s):
    """A file edited while the review is open uploads its current content; nothing is lost."""
    r = s.repo("state01")
    r.write("doc.md", "as reviewed\n")
    r.write("doc.md", "changed while the page was open\n")
    rep, rc = r.apply({"approved_paths": ["doc.md"], "message": "update"}, ["--no-push"])
    eq(rc, 0, rep)
    is_in("doc.md", rep["committed"])
    eq(open(r.path("doc.md")).read(), "changed while the page was open\n",
       "the file on disk is never overwritten by the skill")
    eq(r.git("show", "HEAD:doc.md").stdout, "changed while the page was open\n",
       "and its current content is what got committed")


def state02_no_false_push_claim(s):
    r = s.repo("state02")
    r.write("a.md", "x\n")
    r.git("remote", "set-url", "origin", os.path.join(s.base, "gone.git"))
    rep, _ = r.apply({"approved_paths": ["a.md"], "message": "a"}, ["--no-integrate"])
    assert rep["pushed"] is False
    assert "Synced" not in json.dumps(rep), "must never claim a sync that did not happen"


# ---------- review page ---------------------------------------------------
def review01_endpoint_security(s):
    r = s.repo("review01")
    make_tiff(r, "fig.tiff", 400, 300)
    stage = os.path.join(s.base, "st-review01")
    out, _ = r.convert("image", "fig.tiff", stage)
    secret = os.path.join(s.base, "not-in-plan.txt")
    open(secret, "w").write("must not be served")
    plan = {"root": r.dir, "repository": "RechaviLab/review01", "branch": "main",
            "message": "add preview", "files": [{"path": "notes.md", "size": 100}],
            "conversions": [{"src": "fig.tiff", "output": out["output"],
                             "staged": out["staged"], "original_path": r.path("fig.tiff"),
                             "source_size": out["source_size"],
                             "output_bytes": out["output_bytes"]}],
            "excluded": [{"path": "reads.fastq", "reason": "raw experimental format",
                          "category": "experimental"}]}
    planf = os.path.join(s.base, "plan-review01.json")
    json.dump(plan, open(planf, "w"))
    outf = os.path.join(s.base, "review01-out.json")

    server = review_server(planf, outf, 25)
    with server:
        url = server.url
        base = url.rsplit("/", 2)[0]

        body = urllib.request.urlopen(url, timeout=10).read().decode()
        is_in("Approve all", body, "final actions must be present")
        is_in("reads.fastq", body, "every exclusion must be reachable")
        assert "checkbox" in body and "class=f" in body, "per-file checkboxes must exist"
        assert "class=c value" in body and \
            "checked" not in body.split("class=c")[1][:40], \
            "image conversions must start unselected"

        # only the plan's own token and its own assets are reachable
        for bad in (base + "/", base + "/wrong-token/", url + "asset/deadbeef"):
            try:
                urllib.request.urlopen(bad, timeout=10)
                raise AssertionError("expected refusal for %s" % bad)
            except urllib.error.HTTPError as e:
                assert e.code in (403, 404), (bad, e.code)

        # a request from an unrelated origin is rejected
        foreign = urllib.request.Request(
            url + "submit", method="POST", data=b"{}",
            headers={"Content-Type": "application/json",
                     "Origin": "https://evil.example.com"})
        try:
            urllib.request.urlopen(foreign, timeout=10)
            raise AssertionError("a cross-origin approval must be refused")
        except urllib.error.HTTPError as e:
            eq(e.code, 403)

        req = urllib.request.Request(
            url + "submit", method="POST",
            data=json.dumps({"action": "approve_selected", "files": ["notes.md"],
                             "conversions": ["fig.tiff"],
                             "archive_consent": []}).encode(),
            headers={"Content-Type": "application/json",
                     "Origin": "http://127.0.0.1:1"})
        eq(urllib.request.urlopen(req, timeout=10).read(), b"ok")
        server.wait()

    res = json.load(open(outf))
    eq(res["action"], "approve_selected")
    eq(res["files"], ["notes.md"])
    eq(res["conversions"], ["fig.tiff"])
    eq(open(secret).read(), "must not be served", "an unrelated file is never served")


def review02_close_means_cancel(s):
    r = s.repo("review02")
    plan = {"root": r.dir, "message": "x", "files": [{"path": "a.md"}]}
    planf = os.path.join(s.base, "plan-review02.json")
    json.dump(plan, open(planf, "w"))
    outf = os.path.join(s.base, "review02-out.json")
    proc = subprocess.Popen([sys.executable, os.path.join(SCRIPTS, "review.py"),
                             "--plan", planf, "--out", outf, "--no-browser",
                             "--timeout", "2"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    proc.stdout.readline()
    proc.wait(timeout=30)
    res = json.load(open(outf))
    eq(res["action"], "cancel", "a closed or timed-out page approves nothing")


TESTS = [
    ("SELECT-01 leave one file out", select01_leave_one_out),
    ("UX-07 no-change run makes no commit", ux07_no_change_no_commit),
    ("IMAGE-01 output matches preview", image01_output_matches_preview),
    ("IMAGE-04 edited source is re-offered", image04_edited_source_reoffers),
    ("IMAGE-05 declined, then edited again", image05_declined_then_edited_again),
    ("IMAGE-02 edited output left alone", image02_edited_output_left_alone),
    ("IMAGE-06 both edited -> ask", image06_both_changed_asks),
    ("MEM-01 manifest travels to a clone", mem01_manifest_travels_to_a_clone),
    ("MEM-02 missing manifest: all new", mem02_missing_manifest_treats_all_as_new),
    ("MEM-02b conflicted manifest", mem02b_corrupt_manifest),
    ("MEM-03 deleted output offered again", mem03_deleted_output_offered_again),
    ("GIT-02 disallowed blob unpushed", git02_disallowed_blob_in_unpushed_history),
    ("GIT-04 preserves staged work", git04_preserves_staged_work),
    ("GIT-06 push failure reported", git06_push_failure_reported),
    ("GIT-06b retry makes no duplicate", git06b_retry_makes_no_duplicate),
    ("STATE-01 current content is what syncs", state01_current_content_is_what_syncs),
    ("STATE-02 no false push claim", state02_no_false_push_claim),
    ("REVIEW-01 endpoint security", review01_endpoint_security),
    ("REVIEW-02 close means cancel", review02_close_means_cancel),
]
