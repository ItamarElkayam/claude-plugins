"""End to end: scan -> convert -> review page -> apply, on one realistic mock project."""
import json, os, subprocess, sys, urllib.request
from harness import eq, is_in, sha, SCRIPTS, review_server
from test_guardrail import make_tiff


def e2e01_full_sync(s):
    r = s.repo("e2e")
    # a realistic mix: prose, code, a notebook, a figure source, raw data, slides, a big table
    r.write("notes.md", "# analysis\nfirst results\n")
    r.write("analysis.py", "print('hi')\n")
    r.write("nb.ipynb", '{"cells": []}')
    r.write("obsolete.md", "to be removed later\n")
    r.size_file("reads.fastq", 400_000)
    r.size_file("talk.pptx", 900_000)
    r.size_file("bigtable.csv", 500)
    r.write(".gitignore", "*.tiff\n")
    r.git("add", "notes.md", "analysis.py", "nb.ipynb", "obsolete.md", "bigtable.csv",
          ".gitignore")
    r.git("commit", "-qm", "initial project")
    r.git("push", "-q")

    # now the user works: edits prose, adds a figure, deletes a file
    r.write("notes.md", "# analysis\nfirst results\nsecond pass\n")
    make_tiff(r, "figures/fig1.tiff", 1000, 800)
    r.size_file("bigtable.csv", 1_800_000)      # a tracked table grows past the limit
    os.remove(r.path("obsolete.md"))
    tiff_before = sha(r.path("figures/fig1.tiff"))
    fastq_before = sha(r.path("reads.fastq"))

    # ---- 1. scan --------------------------------------------------------
    sc = r.scan()
    eq(sc["branch"], "main")
    upload = [i for i in sc["items"] if i["decision"] == "upload"]
    convert = [i for i in sc["items"] if i["decision"] == "convert"]
    exclude = [i for i in sc["items"] if i["decision"] == "exclude"]
    eq(sorted(i["path"] for i in upload), ["notes.md", "obsolete.md"])
    eq(r.item(sc, "obsolete.md")["category"], "deletion")
    eq([i["path"] for i in convert], ["figures/fig1.tiff"])
    eq(sorted(i["path"] for i in exclude), ["bigtable.csv", "reads.fastq", "talk.pptx"])
    eq(r.item(sc, "reads.fastq")["reason"], "raw experimental format")
    eq(r.item(sc, "talk.pptx")["reason"], "PowerPoint: managed locally only")
    is_in("figures/", " ".join(sc["ignored_summary"]["entries"]),
          "the ignored tiff source is accounted for")

    # ---- 2. convert -----------------------------------------------------
    stage = os.path.join(s.base, "st-e2e")
    out, rc = r.convert("image", "figures/fig1.tiff", stage)
    eq(rc, 0, out)
    assert out["output_bytes"] <= 1_000_000

    # ---- 3. review page -------------------------------------------------
    plan_for_page = {
        "root": r.dir, "repository": "RechaviLab/e2e", "branch": "main",
        "upstream": "origin/main",
        "message": "Update analysis notes, add fig1 preview, remove obsolete.md",
        "files": [{"path": i["path"], "size": i["size"],
                   "what": i.get("reason") or "edited"} for i in upload],
        "conversions": [{"src": "figures/fig1.tiff", "output": out["output"],
                         "staged": out["staged"],
                         "original_path": r.path("figures/fig1.tiff"),
                         "source_size": out["source_size"],
                         "output_bytes": out["output_bytes"],
                         "source_dims": out["source_dims"],
                         "output_dims": out["output_dims"]}],
        "excluded": [{"path": i["path"], "reason": i["reason"],
                      "category": i["category"]} for i in exclude],
    }
    pf = os.path.join(s.base, "e2e-plan.json")
    json.dump(plan_for_page, open(pf, "w"))
    outf = os.path.join(s.base, "e2e-review.json")
    server = review_server(pf, outf, 30)
    with server:
        url = server.url
        body = urllib.request.urlopen(url, timeout=10).read().decode()
        for expected in ("RechaviLab/e2e", "notes.md", "obsolete.md", "bigtable.csv",
                         "reads.fastq", "talk.pptx", "Approve all", "lab server"):
            is_in(expected, body, "the review page must show %s" % expected)
        is_in("Managed locally only", body, "slides get their own label")

        req = urllib.request.Request(
            url + "submit", method="POST",
            data=json.dumps({"action": "approve_all",
                             "files": ["notes.md", "obsolete.md"],
                             "conversions": ["figures/fig1.tiff"],
                             "archive_consent": []}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        server.wait()
    decision = json.load(open(outf))
    eq(decision["action"], "approve_all")

    # ---- 4. apply -------------------------------------------------------
    plan = {"approved_paths": decision["files"],
            "message": plan_for_page["message"],
            "outputs": [{"src": "figures/fig1.tiff", "staged": out["staged"],
                         "output": out["output"], "source_hash": out["source_hash"],
                         "source_size": out["source_size"]}],
            }
    rep, rc = r.apply(plan, [])
    eq(rc, 0, rep)
    eq(rep["pushed"], True)
    eq(rep["integration"], "clean")
    assert not rep["refused"], rep["refused"]

    # ---- 5. the resulting state ----------------------------------------
    head = r.head_files()
    for must in ("notes.md", "figures/fig1.preview.jpg", ".synctogit.json"):
        is_in(must, head, "%s must be in the repository" % must)
    assert "obsolete.md" not in head, "the user's deletion propagated"
    assert "figures/fig1.tiff" not in head, "the heavy source is never uploaded"
    is_in("bigtable.csv", head, "its previously committed version stays in Git")
    eq(r.git("show", "HEAD:bigtable.csv").stdout.__len__(), 500,
       "but the new oversized content was not uploaded")
    assert "reads.fastq" not in head and "talk.pptx" not in head

    eq(sha(r.path("figures/fig1.tiff")), tiff_before, "the original figure is untouched")
    eq(sha(r.path("reads.fastq")), fastq_before, "raw data is untouched")
    eq(os.path.getsize(r.path("bigtable.csv")), 1_800_000,
       "the user's local table keeps its full contents")
    eq(sha(r.path(out["output"])), out["output_hash"], "committed preview == previewed bytes")

    m = json.load(open(r.path(".synctogit.json")))
    eq(m["conversions"]["figures/fig1.tiff"]["output"], "figures/fig1.preview.jpg")
    is_in("*.tiff", open(r.path(".gitignore")).read(),
          "the type-level rule is all that is needed; no per-file entry is written")

    # ---- 6. an immediately repeated run must be quiet -------------------
    sc2 = r.scan()
    eq(r.item(sc2, "figures/fig1.tiff")["decision"], "skip", "nothing to re-ask")
    rep2, rc2 = r.apply({"approved_paths": [], "message": "again"}, [])
    eq(rc2, 0, rep2)
    assert not rep2["committed"], "a second run must not create an empty commit"

    # ---- 7. the user edits the figure: it comes back for approval -------
    make_tiff(r, "figures/fig1.tiff", 1040, 830)
    sc3 = r.scan()
    it = r.item(sc3, "figures/fig1.tiff")
    eq(it["manifest_state"], "source_changed")
    eq(it["decision"], "convert", "an edited figure must be offered again, not silently stale")


TESTS = [("E2E-01 full sync, then a repeat, then an edited figure", e2e01_full_sync)]
