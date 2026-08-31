#!/usr/bin/env python3
"""paperbase test suite - stdlib only, no network, no model calls.

    python3 tests/run_tests.py            # everything
    python3 tests/run_tests.py -k locator # only matching tests
    python3 tests/run_tests.py -v         # show per-assertion detail

Layers:
  unit        miniyaml, jsonschema_lite, ids, sentences, locator matching, config
  extraction  section hierarchy, running headers, references cut-off, columns,
              multi-format parsing, image-only PDF handling
  pipeline    build, no-op update, add, modify, rename, remove, overrides
  knowledge   ingest validation, provenance, retrieval, context packs, eval
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "scripts"))
sys.path.insert(0, HERE)

import fixtures  # noqa: E402
from paperbase import (bootstrap, build as buildmod, config as cfgmod,  # noqa: E402
                       context as ctxmod, evaluate, extract, fsutil, ids, indexdb,
                       ingest, jsonschema_lite, locators, log, manifest, miniyaml,
                       search, sentences, store, tasks, tune, validate as validatemod)
from paperbase.parsers import pdf_pymupdf  # noqa: E402

TESTS = []
SCRATCH = os.environ.get("PAPERBASE_TEST_DIR") or tempfile.mkdtemp(prefix="paperbase-tests-")


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


class Ctx(object):
    """Shared, lazily built KB used by the pipeline/knowledge tests."""

    def __init__(self):
        self.root = os.path.join(SCRATCH, "corpus")
        self.papers = os.path.join(self.root, "papers")
        self.kb = os.path.join(self.root, "kb")
        self.built = False

    def build(self, force_fresh=False):
        if self.built and not force_fresh:
            return
        if force_fresh and os.path.isdir(self.root):
            shutil.rmtree(self.root)
        os.makedirs(self.papers, exist_ok=True)
        fixtures.build_corpus(self.papers)
        buildmod.build(self.papers, self.kb)
        self.built = True

    def pid(self, needle):
        rows = store.read_corpus_manifest(self.kb)
        for row in rows:
            if needle in row["source_path"]:
                return row["paper_id"]
        raise AssertionError("no paper for %r in %s" % (needle, [r["source_path"] for r in rows]))


CTX = Ctx()


def eq(actual, expected, msg=""):
    assert actual == expected, "%s: expected %r, got %r" % (msg or "value", expected, actual)


def ok(cond, msg):
    assert cond, msg


# ------------------------------------------------------------------ unit tests
@test("unit: miniyaml round-trips the supported subset and errors with line numbers")
def t_miniyaml():
    doc = {"a": "line one\nline two", "b": "trailing\n", "c": [{"x": 1, "y": "two: three"}, "plain"],
           "d": None, "e": True, "f": 3.5, "g": "yes", "h": "", "i": [], "j": {}}
    eq(miniyaml.loads(miniyaml.dumps(doc)), doc, "round-trip")
    eq(miniyaml.loads("a: [MSA, k-mer]\nb: {x: 1, y: two}\n"),
       {"a": ["MSA", "k-mer"], "b": {"x": 1, "y": "two"}},
       "plain (non-JSON) flow collections must parse - people write them in config files")
    fm, body = miniyaml.split_frontmatter("---\nname: x\n---\nbody\n")
    eq(fm["name"], "x", "frontmatter")
    ok(body.strip() == "body", "frontmatter body")
    for bad, needle in (("a: 1\n\tb: 2\n", "tab"), ("key\n", "expected"),
                        ("a: [1, 2\n", "not closed"), ("b: {x: 1\n", "not closed")):
        try:
            miniyaml.loads(bad)
            raise AssertionError("should have failed: %r" % bad)
        except miniyaml.YamlError as exc:
            ok("line" in str(exc) and needle.lower() in str(exc).lower(),
               "error message must name the line and the problem: %s" % exc)


@test("unit: shipped schemas load and reject malformed instances")
def t_schemas():
    schema_dir = os.path.join(SKILL, "schemas")
    names = sorted(n for n in os.listdir(schema_dir) if n.endswith(".json"))
    ok(len(names) >= 7, "expected the full schema set, found %s" % names)
    for name in names:
        jsonschema_lite.load_schema(os.path.join(schema_dir, name)).validate({})
    claim_schema = os.path.join(schema_dir, "claim.schema.json")
    good = {
        "claim_id": "pap-2020-x-abc123#c01-abc123", "paper_id": "pap-2020-x-abc123",
        "statement": "A statement long enough to pass.",
        "claim_type": "empirical_result", "evidence_status": "reported", "polarity": "affirmative",
        "support": [{"paper_id": "pap-2020-x-abc123", "unit_id": "u0001",
                     "excerpt": "a verbatim excerpt here",
                     "extraction_method": "pymupdf", "confidence": 0.9}],
        "confidence": 0.9,
        "generated_by": {"model_id": "m", "prompt": "p.md", "prompt_version": "v", "generated_at": "t"},
    }
    eq(jsonschema_lite.validate(good, claim_schema), [], "valid claim")
    ok(jsonschema_lite.validate(dict(good, support=[]), claim_schema), "empty support must fail")
    ok(jsonschema_lite.validate(dict(good, claim_type="not_a_type"), claim_schema), "bad enum must fail")
    ok(jsonschema_lite.validate(dict(good, oops=1), claim_schema), "unknown property must fail")
    # a schema with an unsupported keyword must raise rather than silently pass
    try:
        jsonschema_lite.Validator({"type": "object", "dependencies": {}}).validate({})
        raise AssertionError("unsupported keyword should raise")
    except jsonschema_lite.SchemaError:
        pass


@test("unit: paper ids are DOI-preferring, stable and readable")
def t_ids():
    meta = {"title": "Alpha: a fast corrector", "authors": ["Ada Alpha"], "year": 2019,
            "doi": "https://doi.org/10.1234/WIDGET.2019.001", "doi_confidence": 0.9}
    pid1, basis1, key1 = ids.paper_id(meta, "a" * 64)
    pid2, _, _ = ids.paper_id(dict(meta, doi="doi: 10.1234/widget.2019.001"), "b" * 64)
    eq(basis1, "doi", "DOI is preferred as identity basis")
    eq(pid1, pid2, "same DOI, different formatting and content hash -> same id")
    ok(pid1.startswith("alpha-2019-"), "id must stay readable: %s" % pid1)
    nometa = {"title": "Untitled scan", "authors": [], "year": None}
    pid3, basis3, _ = ids.paper_id(nometa, "c" * 64)
    eq(basis3, "metadata+content", "fallback identity basis")
    pid4, _, _ = ids.paper_id(nometa, "d" * 64)
    ok(pid3 != pid4, "different content without metadata must not collapse to one id")
    cid = ids.claim_id(pid1, "Some statement", 3)
    eq(cid, ids.claim_id(pid1, "Some  statement", 3), "claim ids are whitespace-stable")


@test("unit: excerpt matching survives reflow, ligatures and control characters")
def t_excerpt_matching():
    text = "BFC is a  free, fast\nand easy-to-\x05use sequencing error corrector for ﬁles."
    span = locators.find_excerpt(text, "free, fast and easy-to-use sequencing")
    ok(span is not None, "excerpt must be found across a newline and a control char")
    ok(locators.find_excerpt(text, "quantum entanglement of widgets") is None,
       "a quote that is not there must NOT be reported as found")
    offs = sentences.split_offsets("One sentence here. E.g. not a break. Second sentence ends here.")
    eq(len(offs), 3, "three sentences, and 'E.g.' must not add a fourth")
    eq(len(sentences.split_offsets("Corrected at 30.5 percent vs. 12 percent in Fig. 2 of ref. 4.")), 1,
       "decimals and abbreviations must not split a single sentence")


@test("unit: config rejects unknown keys and hashes deterministically")
def t_config():
    cfg = cfgmod.load()
    eq(cfgmod.config_hash(cfg), cfgmod.config_hash(cfgmod.load()), "config hash is deterministic")
    try:
        cfgmod._deep_merge(cfg, {"units": {"nope": 1}})
        raise AssertionError("unknown key must be rejected")
    except ValueError as exc:
        ok("units.nope" in str(exc), "error must name the offending key")
    for name in ("paper_analysis.md", "claim_extraction.md", "relation_discovery.md",
                 "topic_synthesis.md", "corpus_overview.md"):
        body = cfgmod.prompt_text(name)
        ok("{{include" not in body, "%s must have its includes expanded" % name)
        ok(len(cfgmod.prompt_version(name)) == 12, "prompt version is a content hash")


# ------------------------------------------------------------------ extraction
@test("extraction: PDF structure - sections, running headers, references, tables")
def t_pdf_structure():
    if not fixtures.HAVE_FITZ:
        raise AssertionError("PyMuPDF unavailable: PDF extraction cannot be tested")
    CTX.build()
    pid = CTX.pid("alpha_2019.pdf")
    parse = store.read_parse(CTX.kb, pid)
    units = store.read_units(CTX.kb, pid)
    text = store.read_text(CTX.kb, pid)
    sections = parse["sections"]
    for want in ("Abstract", "1 Introduction", "2 Methods", "3 Results", "4 Conclusion", "References"):
        ok(any(want in s for s in sections), "section %r not detected in %s" % (want, sections))
    eq(text.count("Widget Analysis"), 0, "the running header (3 pages) must be kept out of the body")
    ok(any(b.get("role") == "header" for b in store.read_blocks(CTX.kb, pid)),
       "header blocks must still be labelled at block level")
    ok(any(b.get("role") == "page_number" for b in store.read_blocks(CTX.kb, pid)),
       "printed page numbers must be recognised, not treated as body text")
    kinds = set(u["kind"] for u in units)
    ok("caption" in kinds, "the table caption must become its own unit (kinds: %s)" % kinds)
    ok(not any("Gamma: greedy widget correction" in u["text"] for u in units),
       "reference list entries must not become retrieval units")
    ok(any("Gamma: greedy widget correction" in b["text"] for b in store.read_blocks(CTX.kb, pid)),
       "reference text must still be preserved at block level")
    for unit in units:
        eq(text[unit["char_start"]:unit["char_end"]], unit["text"],
           "unit %s offsets must index into text.txt" % unit["unit_id"])
        ok(unit["page_label_start"] is not None, "unit must carry a page label")
    intro = [u for u in units if u["section_path"] and "Introduction" in u["section_path"][-1]]
    ok(intro and "greedy" in intro[0]["text"], "two-column reading order put Introduction text in place")


@test("extraction: every declared format parses, unsupported ones are reported")
def t_formats():
    CTX.build()
    rows = {os.path.basename(r["source_path"]): r for r in store.read_corpus_manifest(CTX.kb)}
    for name in ("alpha_2019.pdf", "gamma2_2022.pdf", "epsilon_review.html",
                 "alpha_supplementary.docx", "lab_notes.md", "minutes.txt", "scanned_no_text.pdf"):
        ok(name in rows, "%s was not ingested (got %s)" % (name, sorted(rows)))
    man = manifest.load(CTX.kb)
    unsupported = {u["path"]: u["reason"] for u in man["unsupported"]}
    ok("notes.rtf" in unsupported, "unsupported file must be listed with a reason")
    ok("not a supported document format" in unsupported["notes.rtf"], "reason must be actionable")
    archives = [a["path"] for a in man["archives"]]
    ok("alpha_supplement.zip" in archives, "companion archive must be catalogued")
    ok(man["archives"][0]["contents"], "archive contents must be listed")

    html_pid = rows["epsilon_review.html"]["paper_id"]
    html_text = store.read_text(CTX.kb, html_pid)
    ok("SCRIPTMARKER" not in html_text, "script content must never enter the body")
    ok("NAVMARKER" not in html_text, "nav boilerplate must never enter the body")
    eq(rows["epsilon_review.html"]["metadata"]["doi"], "10.1234/widget.2021.100", "HTML citation DOI")
    eq(rows["epsilon_review.html"]["metadata"]["document_type"], "review", "review detection")
    docx_pid = rows["alpha_supplementary.docx"]["paper_id"]
    ok("--trusted 3" in store.read_text(CTX.kb, docx_pid), "docx table/paragraph text must be captured")
    md_row = rows["lab_notes.md"]
    eq(md_row["metadata"]["doi"], "10.1234/widget.2023.notes", "markdown frontmatter DOI")
    md_units = store.read_units(CTX.kb, md_row["paper_id"])
    ok(any(u.get("line_start") for u in md_units), "non-paginated sources need line anchors")


@test("extraction: image-only PDF warns loudly and invents nothing")
def t_image_only_pdf():
    if not fixtures.HAVE_FITZ:
        raise AssertionError("PyMuPDF unavailable")
    CTX.build()
    pid = CTX.pid("scanned_no_text.pdf")
    parse = store.read_parse(CTX.kb, pid)
    eq(parse["text_quality"], "empty", "image-only PDF must be marked empty")
    ok(parse["needs_ocr"], "needs_ocr must be set")
    joined = " ".join(parse["warnings"]).lower()
    for needle in ("image-only", "ocr", "nothing has been inferred"):
        ok(needle in joined, "warning must mention %r: %s" % (needle, parse["warnings"]))
    eq(store.read_units(CTX.kb, pid), [], "no units may be invented for an empty document")
    eq(store.read_claims(CTX.kb, pid), [], "no claims may exist for an empty document")
    index = fsutil.read_text(os.path.join(CTX.kb, "KB_INDEX.md"))
    ok("scanned_no_text" in index or pid in index, "the KB index must surface the problem document")


# ------------------------------------------------------------------ pipeline
@test("pipeline: build produces the full layer set and never touches the sources")
def t_build_layers():
    CTX.build()
    for rel in ("KB_INDEX.md", ".gitignore", "config/config.yaml", "corpus/papers.jsonl",
                "relations/families.jsonl", "state/manifest.json", "state/audit.jsonl",
                "synthesis/TERMINOLOGY.md", "synthesis/indexes/methods.md",
                "overrides/overrides.yaml", "index/kb.sqlite", "reports/last_build_report.md"):
        ok(os.path.exists(os.path.join(CTX.kb, rel)), "missing KB artifact: %s" % rel)
    before = {}
    for name in sorted(os.listdir(CTX.papers)):
        path = os.path.join(CTX.papers, name)
        before[name] = (os.path.getsize(path), ids.sha256_file(path))
    buildmod.build(CTX.papers, CTX.kb)
    for name, sig in before.items():
        path = os.path.join(CTX.papers, name)
        eq((os.path.getsize(path), ids.sha256_file(path)), sig, "source %s was modified" % name)
    try:
        buildmod.build(CTX.papers, os.path.join(CTX.papers, "kb_inside"))
        raise AssertionError("building inside the paper directory must be refused")
    except SystemExit as exc:
        ok("never be written to" in str(exc), "refusal must explain why")


@test("pipeline: a no-op update re-parses nothing and requests no model work")
def t_noop_update():
    CTX.build()
    man_before = manifest.load(CTX.kb)
    parse_times = {pid: e["stages"]["parse"]["completed_at"]
                   for pid, e in man_before["papers"].items() if e.get("status") == "active"}
    started = time.time()
    report = buildmod.build(CTX.papers, CTX.kb)
    elapsed = time.time() - started
    eq(report["reparsed"], [], "a no-op update must re-parse nothing")
    eq(report["added"], [], "nothing added")
    eq(report["removed"], [], "nothing removed")
    eq(len(report["unchanged"]), len(parse_times), "every paper must be recognised as unchanged")
    man_after = manifest.load(CTX.kb)
    for pid, stamp in parse_times.items():
        eq(man_after["papers"][pid]["stages"]["parse"]["completed_at"], stamp,
           "parse stage for %s must not be re-run" % pid)
    ok(elapsed < 8.0, "no-op update took %.1fs - it should be fast" % elapsed)
    audit = [json.loads(l) for l in fsutil.read_text(
        os.path.join(CTX.kb, "state", "audit.jsonl")).strip().split("\n")]
    ok(audit[-1]["event"] in ("build", "update"), "the run must be audited")
    eq(audit[-1]["reparsed"], 0, "audit must record zero re-parses")


@test("pipeline: modifying a paper invalidates exactly its dependent artifacts")
def t_modify_invalidates():
    CTX.build()
    pid = CTX.pid("gamma2_2022.pdf")
    other = CTX.pid("alpha_2019.pdf")
    _seed_knowledge(CTX.kb, pid)
    _seed_knowledge(CTX.kb, other)
    cfg = cfgmod.load(CTX.kb)
    eq(manifest.analysis_state(manifest.load(CTX.kb), cfg, pid)["claims"], "current", "seeded claims")
    fixtures._pdf_paper_b(os.path.join(CTX.papers, "gamma2_2022.pdf"), changed=True)
    report = buildmod.build(CTX.papers, CTX.kb)
    eq([m["paper_id"] for m in report["modified"]], [pid], "only the edited paper is modified")
    man = manifest.load(CTX.kb)
    state = manifest.analysis_state(man, cfg, pid)
    eq(state["profile"], "stale", "the edited paper's profile must go stale")
    eq(state["claims"], "stale", "the edited paper's claims must go stale")
    eq(manifest.analysis_state(man, cfg, other), {"profile": "current", "claims": "current"},
       "an untouched paper's analysis must stay current")
    ok("relations" in report["stale_corpus_stages"], "corpus relations must be marked stale")
    ok("topics" in report["stale_corpus_stages"], "topic syntheses must be marked stale")
    pending = manifest.model_work_pending(man, cfg)
    ok(pid in pending["profile"] and pid in pending["claims"], "the edited paper must be re-queued")
    ok(other not in pending["profile"], "unchanged papers must not be re-queued")


@test("pipeline: renaming a file keeps ids and knowledge; removing one tombstones cleanly")
def t_rename_and_remove():
    CTX.build(force_fresh=True)
    pid_a = CTX.pid("alpha_2019.pdf")
    pid_b = CTX.pid("gamma2_2022.pdf")
    _seed_knowledge(CTX.kb, pid_a)
    _seed_knowledge(CTX.kb, pid_b)
    _seed_relation(CTX.kb, pid_b, pid_a)

    os.rename(os.path.join(CTX.papers, "alpha_2019.pdf"),
              os.path.join(CTX.papers, "alpha_2019_renamed.pdf"))
    report = buildmod.build(CTX.papers, CTX.kb)
    eq([r["paper_id"] for r in report["renamed"]], [pid_a], "rename must be detected by content hash")
    eq(report["reparsed"], [], "a renamed but unchanged file must not be re-parsed")
    ok(store.read_claims(CTX.kb, pid_a), "claims must survive a rename")
    row = [r for r in store.read_corpus_manifest(CTX.kb) if r["paper_id"] == pid_a][0]
    eq(row["source_path"], "alpha_2019_renamed.pdf", "the manifest must record the new path")

    os.unlink(os.path.join(CTX.papers, "alpha_2019_renamed.pdf"))
    report = buildmod.build(CTX.papers, CTX.kb)
    eq([r["paper_id"] for r in report["removed"]], [pid_a], "removal must be detected")
    man = manifest.load(CTX.kb)
    eq(man["papers"][pid_a]["status"], "tombstoned", "the paper must be tombstoned, not erased")
    ok(any(t["paper_id"] == pid_a for t in man["tombstones"]), "a tombstone record must exist")
    ok(not os.path.exists(store.claims_path(CTX.kb, pid_a)), "claims derived from it must be gone")
    ok(not os.path.exists(store.text_path(CTX.kb, pid_a)), "normalized text must be gone")
    live = store.read_relations(CTX.kb)
    ok(not any(pid_a in (r["source_paper_id"], r.get("target_paper_id")) for r in live),
       "relations pointing at the removed paper must be retired")
    retired = fsutil.read_jsonl(store.tombstoned_relations_path(CTX.kb))
    ok(retired and retired[0]["retired_reason"], "retired relations must be kept with a reason")
    result = validatemod.validate(CTX.kb)
    ok(result["ok"], "after removal the KB must still validate: %s" % result["errors"][:3])
    index = fsutil.read_text(os.path.join(CTX.kb, "KB_INDEX.md"))
    ok("Tombstoned" in index, "the index must disclose tombstoned documents")


@test("pipeline: adding a paper processes only it and re-queues the affected synthesis")
def t_add_paper():
    CTX.build(force_fresh=True)
    for pid in (CTX.pid("alpha_2019.pdf"), CTX.pid("gamma2_2022.pdf")):
        _seed_knowledge(CTX.kb, pid)
    _seed_topic(CTX.kb, CTX.pid("alpha_2019.pdf"))
    cfg = cfgmod.load(CTX.kb)
    eq(manifest.corpus_stale(manifest.load(CTX.kb), "topics",
                             manifest.corpus_fingerprint("topics", cfg, manifest.load(CTX.kb))),
       False, "the seeded topic must start out current")

    with open(os.path.join(CTX.papers, "second_notes.md"), "w", encoding="utf-8") as fh:
        fh.write("# Follow-up widget notes\n\n## Observation\n\n"
                 "A third run of Alpha at 12x coverage again produced more false-positive "
                 "corrections than at 30x, which no published widget paper reports.\n")
    report = buildmod.build(CTX.papers, CTX.kb)
    eq(len(report["added"]), 1, "exactly one paper added")
    eq(len(report["reparsed"]), 1, "only the new paper is parsed")
    man = manifest.load(CTX.kb)
    ok("topics" in report["stale_corpus_stages"], "topic synthesis must be re-queued")
    pending = manifest.model_work_pending(man, cfg)
    new_pid = report["added"][0]["paper_id"]
    ok(new_pid in pending["profile"], "the new paper must be queued for analysis")
    for done in (CTX.pid("alpha_2019.pdf"), CTX.pid("gamma2_2022.pdf")):
        ok(done not in pending["profile"], "already-analysed paper %s must not be re-queued" % done)
        ok(done not in pending["claims"], "already-analysed paper %s must not be re-queued" % done)


@test("pipeline: human overrides survive regeneration and unknown ones are reported")
def t_overrides():
    CTX.build(force_fresh=True)
    pid = CTX.pid("alpha_2019.pdf")
    other = CTX.pid("gamma2_2022.pdf")
    _seed_knowledge(CTX.kb, pid)
    claim_id = store.read_claims(CTX.kb, pid)[0]["claim_id"]
    with open(os.path.join(CTX.kb, "overrides", "corrections.yaml"), "w", encoding="utf-8") as fh:
        fh.write("""papers:
  %s:
    metadata:
      year: 2018
      venue: "Widget Journal"
    topics: ["widget-correction"]
    notes: "year corrected from the journal landing page"
claims:
  %s:
    conditions: ["only at 30x coverage"]
    notes: "the original extraction dropped the coverage condition"
families:
  - source_paper_id: %s
    target_paper_id: %s
    relation: supplement-of
    reason: "human-confirmed"
relations:
  - source_paper_id: %s
    target_paper_id: %s
    relation: cites
    rationale: "A is in B's reference list"
    confidence: 1.0
""" % (pid, claim_id, CTX.pid("alpha_supplementary.docx"), pid, other, pid))
    buildmod.build(CTX.papers, CTX.kb)

    row = [r for r in store.read_corpus_manifest(CTX.kb) if r["paper_id"] == pid][0]
    eq(row["metadata"]["year"], 2018, "metadata override must be applied")
    eq(row["metadata"]["venue"], "Widget Journal", "metadata override must be applied")
    eq(row["metadata_provenance"]["year"]["source"], "human_override", "provenance must say human")
    eq(row["topics"], ["widget-correction"], "topic override must be applied")
    claim = [c for c in store.read_claims(CTX.kb, pid) if c["claim_id"] == claim_id][0]
    eq(claim["conditions"], ["only at 30x coverage"], "claim override must be applied")
    ok(claim["human_override"], "overridden claim must be flagged")
    ok(claim["generated_by"]["reviewed_by_human"], "override implies human review")
    rels = store.read_relations(CTX.kb)
    ok(any(r["relation"] == "cites" and r["origin"] == "human_override" for r in rels),
       "human-authored relation must exist with human_override origin")
    fam = [r for r in rels if r["relation"] == "supplement-of" and r["origin"] == "human_override"]
    ok(fam, "human family link must override the heuristic one")

    # a second build must not lose any of it, and stale ids must be reported not dropped
    buildmod.build(CTX.papers, CTX.kb)
    row = [r for r in store.read_corpus_manifest(CTX.kb) if r["paper_id"] == pid][0]
    eq(row["metadata"]["year"], 2018, "override must survive regeneration")
    with open(os.path.join(CTX.kb, "overrides", "stale.yaml"), "w", encoding="utf-8") as fh:
        fh.write("papers:\n  nonexistent-2020-paper-abcdef:\n    metadata:\n      year: 1999\n")
    report = buildmod.build(CTX.papers, CTX.kb)
    ok(any("nonexistent-2020-paper-abcdef" in item for item in report["unapplied_overrides"]),
       "an override with an unknown id must be reported, never silently dropped")
    result = validatemod.validate(CTX.kb)
    ok(any(i["kind"] == "unapplied_override" for i in result["warnings"]),
       "validation must surface unapplied overrides")


# ------------------------------------------------------------------ knowledge
@test("knowledge: ingest rejects unverifiable excerpts, missing locators and bad schemas")
def t_ingest_guards():
    CTX.build()
    pid = CTX.pid("alpha_2019.pdf")
    units = store.read_units(CTX.kb, pid)
    good_unit = units[2]["unit_id"]
    good_excerpt = units[2]["text"][:60]

    errs, _ = ingest.ingest_claims(CTX.kb, pid, [dict(_claim_template(pid, good_unit, good_excerpt),
                                                      support=[])], "test-model")
    ok(any("no source locator" in e for e in errs), "a claim without a locator must be rejected")
    errs, _ = ingest.ingest_claims(CTX.kb, pid, [dict(_claim_template(pid, good_unit, good_excerpt),
                                                      claim_type="invented_type")], "test-model")
    ok(any("enum" in e for e in errs), "a bad enum value must be rejected: %s" % errs)
    errs, _ = ingest.ingest_claims(CTX.kb, pid, [dict(_claim_template(pid, good_unit, good_excerpt),
                                                      evidence_status="calculated")], "test-model")
    ok(any("derivation" in e for e in errs), "calculated claims must show their derivation")

    _, warns = ingest.ingest_claims(
        CTX.kb, pid, [_claim_template(pid, good_unit, "a quotation that does not appear anywhere")],
        "test-model")
    ok(any("excerpt not found" in w for w in warns), "an unverifiable excerpt must warn: %s" % warns)
    claim = store.read_claims(CTX.kb, pid)[0]
    ok(claim["support"][0]["confidence"] <= 0.3, "unverified locators must lose confidence")
    result = validatemod.validate(CTX.kb)
    ok(any(i["kind"] == "excerpt_mismatch" for i in result["errors"]),
       "validation must fail on an unverifiable excerpt")

    errs, _ = ingest.ingest_claims(CTX.kb, pid, [_claim_template(pid, good_unit, good_excerpt)],
                                   "test-model")
    eq(errs, [], "a well-formed claim must be accepted: %s" % errs)
    installed = store.read_claims(CTX.kb, pid)[0]
    loc = installed["support"][0]
    ok(loc["char_start"] is not None and loc["page_label"], "locator must be completed on ingest")
    text = store.read_text(CTX.kb, pid)
    ok(locators.find_excerpt(text, loc["excerpt"]), "installed excerpt must verify")
    eq(installed["generated_by"]["model_id"], "test-model", "provenance must record the model")
    ok(installed["generated_by"]["prompt_version"], "provenance must record the prompt version")


@test("knowledge: synthesis must cite claim ids and may not reference unknown ids")
def t_synthesis_grounding():
    CTX.build(force_fresh=True)
    pid = CTX.pid("alpha_2019.pdf")
    _seed_knowledge(CTX.kb, pid)
    claim_id = store.read_claims(CTX.kb, pid)[0]["claim_id"]
    topic = {"name": "Widget precision", "summary": "S" * 60, "papers": [pid],
             "agreements": [{"text": "A grounded synthesis point about widgets.",
                             "claim_ids": ["does-not-exist#c99-abcdef"]}]}
    errs, _ = ingest.ingest_topics(CTX.kb, [topic], "test-model")
    ok(any("unknown reference" in e for e in errs), "unknown claim id must be rejected: %s" % errs)
    topic["agreements"][0]["claim_ids"] = [claim_id]
    errs, _ = ingest.ingest_topics(CTX.kb, [topic], "test-model")
    eq(errs, [], "a grounded topic must be accepted: %s" % errs)
    ungrounded = dict(topic, name="Ungrounded topic",
                      agreements=[{"text": "A point with no claim ids at all.", "claim_ids": []}])
    errs, _ = ingest.ingest_topics(CTX.kb, [ungrounded], "test-model")
    ok(errs, "a synthesis point with an empty claim list must be rejected")
    overview = {"field_summary": "F" * 100,
                "strong_agreement": [{"text": "An unsupported corpus-level claim.", "claim_ids": []}]}
    errs, _ = ingest.ingest_overview(CTX.kb, overview, "test-model")
    ok(errs or any(True for _ in []), "overview with empty claim_ids must not pass validation")
    ingest.ingest_overview(CTX.kb, {"field_summary": "F" * 100,
                                    "strong_agreement": [{"text": "A grounded corpus claim.",
                                                          "claim_ids": [claim_id]}]}, "test-model")
    result = validatemod.validate(CTX.kb)
    ok(not any(i["kind"] == "unsupported_synthesis" for i in result["errors"] + result["warnings"]),
       "grounded synthesis must validate cleanly")


@test("knowledge: contradiction relations require the alternative explanations")
def t_contradiction_discipline():
    CTX.build(force_fresh=True)
    a, b = CTX.pid("alpha_2019.pdf"), CTX.pid("gamma2_2022.pdf")
    _seed_knowledge(CTX.kb, a)
    _seed_knowledge(CTX.kb, b)
    ca = store.read_claims(CTX.kb, a)[0]["claim_id"]
    cb = store.read_claims(CTX.kb, b)[0]["claim_id"]
    rel = {"source_paper_id": b, "target_paper_id": a, "source_claim_id": cb, "target_claim_id": ca,
           "relation": "contradicts", "dimension": "false positives",
           "rationale": "B reports the opposite of A on precision.", "confidence": 0.6,
           "origin": "inferred_by_analysis"}
    errs, _ = ingest.ingest_relations(CTX.kb, [rel], "test-model")
    ok(any("alternative explanations" in e for e in errs),
       "a contradiction without difference_explanations must be rejected: %s" % errs)
    rel["difference_explanations"] = ["simulated vs real data", "per-base vs per-read FP definition"]
    errs, _ = ingest.ingest_relations(CTX.kb, [rel], "test-model")
    eq(errs, [], "a properly justified contradiction must be accepted: %s" % errs)
    errs, _ = ingest.ingest_relations(CTX.kb, [dict(rel, target_paper_id="ghost-2020-paper-abcdef")],
                                      "test-model")
    ok(any("not an active paper" in e for e in errs), "relations to unknown papers must be rejected")


@test("retrieval: single-paper factual query returns the right claim and source location")
def t_retrieval_single():
    CTX.build(force_fresh=True)
    a = CTX.pid("alpha_2019.pdf")
    _seed_knowledge(CTX.kb, a, indel=True)
    buildmod.build(CTX.papers, CTX.kb)
    conn = indexdb.connect(CTX.kb, create=False)
    rows = search.search_claims(conn, indexdb.fts_query("indel correction implemented widget"),
                                5, search.Filters())
    conn.close()
    ok(rows, "the indel claim must be retrievable")
    hit = rows[0]
    ok("indel" in hit["statement"].lower(), "top claim must be the indel claim: %s" % hit["statement"])
    loc = json.loads(hit["support"])[0]
    eq(loc["paper_id"], a, "locator must point at the right paper")
    ok(loc["unit_id"] and loc["page_label"], "locator must carry unit and page")
    text = store.read_text(CTX.kb, a)
    ok(locators.find_excerpt(text, loc["excerpt"]), "locator excerpt must resolve in the source")
    eq(text[loc["char_start"]:loc["char_end"]].strip().lower()[:20],
       loc["excerpt"].strip().lower()[:20], "char offsets must land on the excerpt")


@test("retrieval: cross-paper and disagreement queries return both sides with citations")
def t_retrieval_cross():
    CTX.build(force_fresh=True)
    a, b = CTX.pid("alpha_2019.pdf"), CTX.pid("gamma2_2022.pdf")
    _seed_knowledge(CTX.kb, a, indel=True)
    _seed_knowledge(CTX.kb, b)
    _seed_relation(CTX.kb, b, a, relation="partially-contradicts")
    _seed_topic(CTX.kb, a, extra_paper=b)
    buildmod.build(CTX.papers, CTX.kb)
    cfg = cfgmod.load(CTX.kb)

    pack, strategy = ctxmod.build_context(
        CTX.kb, "Do Alpha and Gamma 2.0 disagree about false-positive corrections?", cfg,
        budget_tokens=6000)
    ok(a in pack and b in pack, "both papers must appear in a cross-paper pack")
    ok("## Counterevidence" in pack, "the pack must carry a counterevidence section")
    ok(strategy["included"]["counterevidence"] >= 1, "opposing evidence must actually be included")
    ok("partially-contradicts" in pack, "the disagreement relation must be surfaced")
    ok("simulated" in pack.lower(), "the reason for the disagreement must be visible")
    ok("[SYNTHESIS" in pack, "model-generated synthesis must be labelled as such")
    ok("Where to look next" in pack, "the pack must point at further reading")
    ok("Retrieval strategy" in pack, "the pack must disclose its retrieval strategy")
    eq(strategy["budget"]["pack_tokens_estimate"] <= 6000, True,
       "the pack must honour its token budget (%s)" % strategy["budget"])

    for cid in set(evaluate.CLAIM_RE.findall(pack)):
        claim = _find_claim(CTX.kb, cid)
        ok(claim is not None, "pack cites unknown claim %s" % cid)
        for loc in claim["support"]:
            ok(locators.find_excerpt(store.read_text(CTX.kb, claim["paper_id"]), loc["excerpt"]),
               "cited claim %s has an unverifiable excerpt" % cid)

    filtered, fstrategy = ctxmod.build_context(CTX.kb, "false-positive corrections", cfg,
                                               budget_tokens=4000,
                                               filters=search.Filters(papers=[a]))
    passages = filtered.split("## Source passages")[1].split("## Papers in this pack")[0]
    ok(b not in passages, "a paper filter must keep other papers' passages out")
    claims_section = filtered.split("## Relevant claims")[1].split("## Counterevidence")[0]
    for cid in set(evaluate.CLAIM_RE.findall(claims_section)):
        ok(cid.startswith(a), "a paper filter must keep other papers' claims out: %s" % cid)
    ok("NOT restricted by the filters" in filtered,
       "the pack must disclose which sections the filter does not apply to")
    ok("filters: papers=" in filtered, "the pack must disclose the filters that were applied")


@test("evaluation: the shipped question template runs and scores a real pack")
def t_eval_harness():
    CTX.build(force_fresh=True)
    a, b = CTX.pid("alpha_2019.pdf"), CTX.pid("gamma2_2022.pdf")
    _seed_knowledge(CTX.kb, a, indel=True)
    _seed_knowledge(CTX.kb, b)
    _seed_relation(CTX.kb, b, a, relation="partially-contradicts")
    buildmod.build(CTX.papers, CTX.kb)
    qpath = os.path.join(SCRATCH, "questions.yaml")
    with open(qpath, "w", encoding="utf-8") as fh:
        fh.write("""questions:
  - id: q1
    question: "Does Alpha implement indel correction?"
    expect_papers: ["%s"]
    must_include_terms: ["indel"]
    expect_counterevidence: false
    budget: 4000
  - id: q2
    question: "How do Alpha and Gamma 2.0 compare on false-positive corrections?"
    expect_papers: ["%s", "%s"]
    expect_counterevidence: true
    budget: 6000
""" % (a, a, b))
    result = evaluate.run(CTX.kb, qpath, budget=6000)
    eq(result["n_questions"], 2, "both questions must run")
    for row in result["questions"]:
        eq(row["expected_papers_found"], 1.0, "%s paper recall: %s" % (row["id"], row["notes"]))
        eq(row["locator_validity"] in (None, 1.0), True,
           "%s locator validity must be perfect: %s" % (row["id"], row["locator_validity"]))
        ok(row["citation_coverage"] > 0.5, "%s citation coverage too low" % row["id"])
    ok(os.path.exists(result["report_path"]), "an evaluation report must be written")


@test("robustness: interrupted runs are recoverable and writes are atomic")
def t_atomicity():
    CTX.build()
    path = os.path.join(SCRATCH, "atomic.json")
    fsutil.atomic_write_json(path, {"a": 1})
    try:
        fsutil.atomic_write_text(path, None)  # type: ignore - forced failure mid-write
    except Exception:
        pass
    eq(fsutil.read_json(path), {"a": 1}, "a failed write must leave the previous version intact")
    ok(not [n for n in os.listdir(SCRATCH) if n.startswith(".pb-tmp-")], "no temp files may remain")
    fsutil.journal_begin(CTX.kb, "parse", ["x.pdf"])
    result = validatemod.validate(CTX.kb)
    ok(any("interrupted" in i["message"] for i in result["warnings"]),
       "an interrupted stage must be reported by validate")
    fsutil.journal_end(CTX.kb)
    ok(not fsutil.journal_state(CTX.kb), "the journal must clear")


@test("tasks: work orders carry the prompt, schema, units and response path")
def t_work_orders():
    CTX.build(force_fresh=True)
    pid = CTX.pid("alpha_2019.pdf")
    orders = tasks.emit(CTX.kb, "profile", paper_ids=[pid])
    eq(len(orders), 1, "one work order expected")
    order = orders[0]
    body = fsutil.read_text(order["order_path"])
    for needle in ("paperbase work order", "schemas/paper_profile.schema.json".split("/")[-1],
                   "Retrieval units", "u0001", "kb ingest"):
        ok(needle in body, "work order must contain %r" % needle)
    ok("verbatim" in body.lower(), "the prompt's fidelity rules must be embedded")
    payload = fsutil.read_json(order["order_path"].replace(".order.md", ".order.json"))
    ok(payload["input"]["units"], "machine-readable units must accompany the order")
    eq(payload["prompt_version"], cfgmod.prompt_version("paper_analysis.md"), "prompt version recorded")
    ok(order["response_path"].endswith("%s.response.json" % pid), "response path must be explicit")




# ------------------------------------------------------------------ setup + tuning
@test("setup: doctor reports the environment and installs only what the corpus needs")
def t_doctor():
    report = bootstrap.doctor(needed_capabilities=[])
    ok(report["python_ok"], "python version check")
    ok(report["sqlite"]["fts5"], "FTS5 must be available for search to work")
    ok(bootstrap.libdir().endswith(os.path.join("paperbase", "pylibs")),
       "installs must target a private dir, got %s" % bootstrap.libdir())
    ok("pdftoppm" in report["optional_tools"], "optional OCR tools must be reported")
    text = bootstrap.format_report(report)
    ok("private libdir" in text and "auto-install" in text, "report must be legible")

    # a corpus with no PDFs must not trigger any install
    eq(bootstrap.ensure_for_corpus([".md", ".txt"])["needed"], [], "no PDFs -> no install")
    eq(bootstrap.ensure_for_corpus([".pdf"])["needed"], ["pdf"], "PDFs -> pdf capability")

    # opt-out must be honoured and must explain how to proceed by hand
    old = os.environ.get("PAPERBASE_NO_INSTALL")
    os.environ["PAPERBASE_NO_INSTALL"] = "1"
    try:
        ok(not bootstrap.installs_allowed(), "PAPERBASE_NO_INSTALL must disable installs")
    finally:
        if old is None:
            del os.environ["PAPERBASE_NO_INSTALL"]
        else:
            os.environ["PAPERBASE_NO_INSTALL"] = old
    ok(not bootstrap.installs_allowed(no_install=True), "--no-install must disable installs")
    # when a capability is missing AND installs are off, the message must be actionable
    saved = bootstrap.REQUIREMENTS.get("_probe")
    bootstrap.REQUIREMENTS["_probe"] = [
        {"module": "definitely_not_installed_xyz", "spec": "definitely-not-installed-xyz",
         "why": "test"}]
    try:
        outcome = bootstrap.ensure("_probe", no_install=True)
        eq(outcome["satisfied_by"], None, "unavailable capability")
        joined = " ".join(outcome["actions"])
        ok("pip install" in joined and "--target" in joined,
           "must tell the user the exact command: %s" % joined)
    finally:
        if saved is None:
            bootstrap.REQUIREMENTS.pop("_probe", None)
        else:
            bootstrap.REQUIREMENTS["_probe"] = saved


@test("tuning: the corpus's own vocabulary is mined automatically")
def t_tuning_mining():
    CTX.build(force_fresh=True)
    mined = fsutil.read_json(os.path.join(CTX.kb, "reports", "tuning_report.json"), {})
    ok(mined, "a tuning report must be written on build")
    ok("MSA" in mined["acronyms"], "the acronym defined in the corpus must be mined: %s"
       % sorted(mined["acronyms"]))
    eq(mined["acronyms"]["MSA"]["expansion"], "multiple sequence alignment", "expansion")
    terms = [t["term"] for t in mined["priority_terms"]]
    ok(terms, "corpus vocabulary must not be empty")
    ok(all(len(t.split()) >= 2 for t in terms), "priority terms are multi-word: %s" % terms)
    # mining reads retrieval units, so reference-list text must not become vocabulary
    ok(not any("widget j" in t for t in terms), "reference-list text leaked into vocabulary: %s" % terms)
    cfg = cfgmod.load(CTX.kb)
    eq(cfg["terminology"]["acronyms"].get("MSA"), "multiple sequence alignment",
       "mined acronym must reach the effective config")
    index = fsutil.read_text(os.path.join(CTX.kb, "KB_INDEX.md"))
    ok("Corpus vocabulary" in index, "the KB index must surface the mined vocabulary")


@test("tuning: derived config is layered under the human config, which always wins")
def t_config_layering():
    CTX.build(force_fresh=True)
    human_path = cfgmod.kb_config_path(CTX.kb)
    ok(os.path.exists(human_path), "a config template must be installed")
    eq(miniyaml.load(human_path), None,
       "the installed template must be fully commented out, or it would pin every default "
       "and silently defeat automatic tuning")
    cfg = cfgmod.load(CTX.kb)
    eq(cfg["units"]["max_chars"], 1500, "defaults still apply")
    ok(cfg["terminology"]["synonyms"], "mined synonyms apply when the human is silent")
    with open(human_path, "a", encoding="utf-8") as fh:
        fh.write("\nterminology:\n  synonyms: {}\nunits:\n  max_chars: 900\n")
    cfg = cfgmod.load(CTX.kb)
    eq(cfg["units"]["max_chars"], 900, "human value must win over the default")
    eq(cfg["terminology"]["synonyms"], {}, "human {} must suppress the mined value")
    with open(human_path, "a", encoding="utf-8") as fh:
        fh.write("\nparsing:\n  colum_min_blocks: 3\n")
    try:
        cfgmod.load(CTX.kb)
        raise AssertionError("a typo in the human config must be rejected")
    except SystemExit as exc:
        ok("colum_min_blocks" in str(exc), "the error must name the offending key")


@test("tuning: a recurring body-size heading is adopted, re-segments once, then settles")
def t_heading_adoption():
    CTX.build(force_fresh=True)
    cfg = cfgmod.load(CTX.kb)
    eq(cfg["parsing"]["extra_section_headings"], ["Widget calibration"],
       "a heading recurring across papers at body font size must be adopted")
    pid = CTX.pid("alpha_2019.pdf")
    ok("Widget calibration" in store.read_parse(CTX.kb, pid)["sections"],
       "the corpus must be re-segmented so the adopted heading starts a section")

    second = buildmod.build(CTX.papers, CTX.kb)
    eq(second["reparsed"], [], "the second build must not re-parse again (no oscillation)")
    derived_before = fsutil.read_text(cfgmod.derived_path(CTX.kb))
    third = buildmod.build(CTX.papers, CTX.kb)
    eq(third["reparsed"], [], "and neither must the third")
    eq(fsutil.read_text(cfgmod.derived_path(CTX.kb)), derived_before,
       "derived config must be stable across updates")

    # a human veto reverses the adoption
    with open(cfgmod.kb_config_path(CTX.kb), "a", encoding="utf-8") as fh:
        fh.write("\nparsing:\n  extra_section_headings: []\n")
    buildmod.build(CTX.papers, CTX.kb)
    ok("Widget calibration" not in store.read_parse(CTX.kb, pid)["sections"],
       "setting extra_section_headings: [] must undo the adoption")
    CTX.built = False  # this KB now carries a human override; rebuild for later tests


@test("retrieval: mined acronyms expand queries in both directions")
def t_synonym_expansion():
    CTX.build(force_fresh=True)
    cfg = cfgmod.load(CTX.kb)
    conn = indexdb.connect(CTX.kb, create=False)
    try:
        expanded = indexdb.expand_synonyms(conn, "how is the MSA scored?", cfg)
        ok(any("multiple sequence alignment" in e for e in expanded),
           "querying the acronym must add the expansion: %s" % expanded)
        expanded = indexdb.expand_synonyms(conn, "multiple sequence alignment consensus", cfg)
        ok("MSA" in expanded, "querying the expansion must add the acronym: %s" % expanded)
        # word-boundary matching: a short alias must not fire inside another word
        cfg2 = dict(cfg)
        cfg2["terminology"] = dict(cfg["terminology"],
                                   synonyms={"true positive": ["TP"]}, acronyms={"TP": "true positive"})
        eq(indexdb.expand_synonyms(conn, "the output of the pipeline", cfg2), [],
           "alias 'TP' must not match inside 'output'")
        ok(indexdb.expand_synonyms(conn, "TP and FP counts", cfg2), "but must match as a word")
        rows = search.search_units(conn, indexdb.fts_query("MSA") + ' OR "multiple sequence alignment"',
                                   5, search.Filters())
        ok(rows, "expansion must actually retrieve the passage that spells the term out")
    finally:
        conn.close()


# ------------------------------------------------------------------ helpers
def _claim_template(pid, unit_id, excerpt):
    return {
        "statement": "Alpha did not implement indel correction for widget reads.",
        "claim_type": "limitation", "evidence_status": "reported", "polarity": "negative",
        "evidence_type": "expert_assertion", "is_primary_evidence": True,
        "conditions": [], "qualifiers": [], "concepts": ["indel"], "methods": ["Alpha"],
        "support": [{"unit_id": unit_id, "excerpt": excerpt, "extraction_method": "pymupdf",
                     "confidence": 0.9}],
        "confidence": 0.9,
    }


def _seed_knowledge(kb, pid, indel=False):
    """Install a minimal but schema-valid profile + claims for a paper."""
    units = store.read_units(kb, pid)
    ok(units, "cannot seed knowledge for %s: no units" % pid)
    unit = units[min(2, len(units) - 1)]
    excerpt = unit["text"][:80]
    loc = [{"unit_id": unit["unit_id"], "excerpt": excerpt, "extraction_method": "pymupdf",
            "confidence": 0.9}]
    profile = {"analysis": {
        "research_problem": {"text": "Widget correction introduces false-positive corrections.",
                             "basis": "author_stated", "locators": loc},
        "main_contribution": {"text": "A corrector for widget reads.", "basis": "author_stated",
                              "locators": loc},
        "study_type": {"value": "method_or_tool", "basis": "shown_by_result", "locators": loc},
        "primary_findings": [{"text": "Fewer false-positive corrections than the comparator.",
                              "basis": "author_stated", "locators": loc}],
        "limitations_inferred": [{"text": "Evaluated on a single dataset.", "basis": "agent_inferred",
                                  "rationale": "Only one dataset appears in the paper.",
                                  "locators": loc}],
        "data_sources": [{"name": "widget reads at 30x coverage", "kind": "dataset",
                          "role": "evaluation-data", "basis": "author_stated", "locators": loc}],
        "retrieval_terms": ["widget", "false-positive corrections"],
        "unavailable_fields": [],
    }}
    errs, _ = ingest.ingest_profile(kb, pid, profile, "test-model")
    eq(errs, [], "seed profile must validate: %s" % errs)
    claims = [{
        "statement": "On widget data this tool reduced false-positive corrections relative to its comparator.",
        "claim_type": "performance_comparison", "evidence_status": "reported",
        "polarity": "affirmative", "subject": "widget reads at 30x coverage",
        "outcome": "false-positive corrections", "evidence_type": "benchmark",
        "is_primary_evidence": True, "conditions": ["30x coverage"], "qualifiers": ["single dataset"],
        "concepts": ["false-positive corrections"], "methods": ["widget corrector"],
        "support": loc, "confidence": 0.9,
    }]
    if indel:
        indel_unit = None
        for cand in units:
            if "indel" in cand["text"].lower():
                indel_unit = cand
                break
        ok(indel_unit is not None, "fixture must contain an indel sentence")
        start = indel_unit["text"].lower().index("we did not implement")
        claims.append(_claim_template(pid, indel_unit["unit_id"],
                                      indel_unit["text"][start:start + 90]))
    errs, _ = ingest.ingest_claims(kb, pid, claims, "test-model")
    eq(errs, [], "seed claims must validate: %s" % errs)


def _seed_relation(kb, src, dst, relation="compares-with"):
    ca = store.read_claims(kb, src)[0]["claim_id"]
    cb = store.read_claims(kb, dst)[0]["claim_id"]
    rel = {"source_paper_id": src, "target_paper_id": dst, "source_claim_id": ca,
           "target_claim_id": cb, "relation": relation, "dimension": "false-positive corrections",
           "rationale": "Both report false-positive corrections for widget data.",
           "confidence": 0.7, "origin": "inferred_by_analysis"}
    if relation in ("contradicts", "partially-contradicts"):
        rel["difference_explanations"] = ["simulated versus real widget data",
                                          "per-base versus per-read false-positive definition"]
    errs, _ = ingest.ingest_relations(kb, [rel], "test-model")
    eq(errs, [], "seed relation must validate: %s" % errs)


def _seed_topic(kb, pid, extra_paper=None):
    claim_ids = [store.read_claims(kb, pid)[0]["claim_id"]]
    papers = [pid]
    if extra_paper:
        papers.append(extra_paper)
        claim_ids.append(store.read_claims(kb, extra_paper)[0]["claim_id"])
    topic = {
        "name": "False-positive corrections in widget correction",
        "summary": "Both tools target precision, but they measure false positives differently, "
                   "so their headline numbers are not directly comparable.",
        "papers": papers,
        "agreements": [{"text": "Precision, not sensitivity, distinguishes widget correctors.",
                        "claim_ids": claim_ids, "evidence_strength": "moderate"}],
        "apparent_disagreements_explained": [
            {"text": "The tools' precision claims differ because one uses simulated data with "
                     "per-base counting and the other real data with per-read counting.",
             "claim_ids": claim_ids, "evidence_strength": "contested"}],
        "open_questions": [{"text": "How do the tools rank under one shared definition?",
                            "claim_ids": claim_ids}],
    }
    errs, _ = ingest.ingest_topics(kb, [topic], "test-model")
    eq(errs, [], "seed topic must validate: %s" % errs)


def _find_claim(kb, claim_id):
    pid = claim_id.split("#")[0]
    for claim in store.read_claims(kb, pid):
        if claim["claim_id"] == claim_id:
            return claim
    return None


# ------------------------------------------------------------------ runner
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-k", "--filter", help="only run tests whose name contains this")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--keep", action="store_true", help="keep the scratch directory")
    args = parser.parse_args(argv)
    log.set_level(quiet=not args.verbose, verbose=False)

    selected = [(n, f) for n, f in TESTS if not args.filter or args.filter in n]
    print("paperbase tests: %d selected (scratch: %s)\n" % (len(selected), SCRATCH))
    passed, failed = 0, []
    for name, fn in selected:
        started = time.time()
        try:
            fn()
            passed += 1
            print("  PASS  %-72s %.2fs" % (name, time.time() - started))
        except Exception as exc:
            failed.append((name, exc, traceback.format_exc()))
            print("  FAIL  %-72s %.2fs" % (name, time.time() - started))
            print("        %s" % str(exc).split("\n")[0][:200])
    print("\n%d passed, %d failed" % (passed, len(failed)))
    for name, exc, tb in failed:
        print("\n--- %s ---\n%s" % (name, tb if args.verbose else str(exc)))
    if not args.keep and not failed and "PAPERBASE_TEST_DIR" not in os.environ:
        shutil.rmtree(SCRATCH, ignore_errors=True)
    else:
        print("\nscratch kept at %s" % SCRATCH)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
