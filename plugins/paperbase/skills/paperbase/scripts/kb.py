#!/usr/bin/env python3
"""paperbase CLI - build and query a scientific-literature knowledge base.

    kb build <paper-dir> <kb-dir>        parse, normalize, index (no model calls)
    kb update <paper-dir> <kb-dir>       incremental: only what changed
    kb status <kb-dir>                   what exists, what is stale, what is pending
    kb tasks <kb-dir> --stage <stage>    emit work orders for model-dependent stages
    kb ingest <kb-dir> --stage <stage>   validate + install a model's answer
    kb search <kb-dir> "<query>"         lexical search with filters
    kb context <kb-dir> "<question>"     compact evidence pack for another agent
    kb show-paper <kb-dir> <paper_id>    one paper's profile, claims, relations
    kb show-claim <kb-dir> <claim_id>    one claim with its exact source passage
    kb validate <kb-dir>                 schema + provenance + link validation
    kb reindex <kb-dir>                  rebuild the derived SQLite index
    kb eval <kb-dir> [questions.yaml]    run retrieval evaluation questions

Run `kb <command> --help` for options.  Everything is local; nothing is uploaded.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from paperbase import (PIPELINE_VERSION, SCHEMA_VERSION, build as buildmod,
                       config as cfgmod, context as ctxmod, evaluate, fsutil,
                       indexdb, ingest as ingestmod, locators, log, manifest,
                       render, search as searchmod, store, tasks as tasksmod,
                       validate as validatemod)


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-q", "--quiet", action="store_true", help="errors only")
    parser.add_argument("-v", "--verbose", action="store_true", help="per-document detail")


def _filters_from_args(args) -> searchmod.Filters:
    return searchmod.Filters(
        papers=getattr(args, "paper", None), years=[int(y) for y in (getattr(args, "year", None) or [])],
        doc_types=getattr(args, "type", None), methods=getattr(args, "method", None),
        datasets=getattr(args, "dataset", None), concepts=getattr(args, "concept", None),
        authors=getattr(args, "author", None), claim_types=getattr(args, "claim_type", None),
        min_confidence=getattr(args, "min_confidence", None),
        primary_only=getattr(args, "primary_only", False),
        exclude_papers=getattr(args, "exclude_paper", None),
    )


# ---------------------------------------------------------------- commands
def cmd_build(args) -> int:
    report = buildmod.build(args.paper_dir, args.kb_dir, force=args.force,
                            copy_sources=True if args.copy_sources else None)
    _print_build(report)
    return 0


def cmd_update(args) -> int:
    report = buildmod.build(args.paper_dir or _paper_dir(args.kb_dir), args.kb_dir,
                            force=args.force)
    _print_build(report)
    return 0


def _paper_dir(kb_dir: str) -> str:
    man = manifest.load(kb_dir)
    if not man:
        raise SystemExit("no KB at %s - use `kb build <paper-dir> <kb-dir>` first" % kb_dir)
    return man["paper_dir"]


def _print_build(report) -> None:
    log.info("%s complete in %.1fs", report["mode"], report.get("elapsed_s", 0))
    for key in ("added", "modified", "renamed", "removed", "unchanged", "reparsed", "failed", "id_changed"):
        items = report.get(key) or []
        if items:
            log.info("  %-10s %d", key, len(items))
            for item in items[: (None if log.level() >= log.VERBOSE else 5)]:
                log.debug("    %s", item)
    counts = report.get("counts") or {}
    log.info("  index: %s", ", ".join("%s=%d" % kv for kv in sorted(counts.items())))
    pending = {k: v for k, v in (report.get("model_work_pending") or {}).items() if v}
    if pending:
        log.info("  model work pending: %s", ", ".join(
            "%s(%s)" % (k, "corpus" if v == ["*"] else len(v)) for k, v in pending.items()))
        log.info("  -> emit work orders with: kb tasks %s --stage <stage>", report["kb_dir"])
    else:
        log.info("  model work pending: none (no-op update did zero model work)")
    for warning in (report.get("warnings") or [])[:10]:
        log.warn(warning)
    log.info("  report: %s", os.path.join(report["kb_dir"], "reports",
                                          "last_%s_report.md" % report["mode"]))


def cmd_status(args) -> int:
    kb_dir = args.kb_dir
    man = manifest.load(kb_dir)
    if not man:
        raise SystemExit("no KB at %s" % kb_dir)
    cfg = cfgmod.load(kb_dir)
    active = manifest.active_papers(man)
    pending = manifest.model_work_pending(man, cfg)
    rows = store.read_corpus_manifest(kb_dir)
    out = {
        "kb_dir": kb_dir,
        "paper_dir": man["paper_dir"],
        "schema_version": man["schema_version"],
        "pipeline_version": man["pipeline_version"],
        "config_hash": man["config_hash"],
        "updated": man["updated"],
        "papers_active": len(active),
        "papers_tombstoned": len(man.get("tombstones") or []),
        "unsupported_files": len(man.get("unsupported") or []),
        "archives": len(man.get("archives") or []),
        "profiles": sum(1 for r in rows if r.get("has_profile")),
        "claims": sum(len(store.read_claims(kb_dir, pid)) for pid in active),
        "relations": len(store.read_relations(kb_dir)),
        "topics": len(store.read_topics(kb_dir)),
        "overview": bool(store.read_overview(kb_dir)),
        "pending_model_work": {k: v for k, v in pending.items() if v},
        "interrupted_stage": fsutil.journal_state(kb_dir) or None,
    }
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    print("paperbase KB: %s" % kb_dir)
    print("  papers dir      %s" % out["paper_dir"])
    print("  versions        schema %s / pipeline %s / config %s" % (
        out["schema_version"], out["pipeline_version"], out["config_hash"]))
    print("  updated         %s" % out["updated"])
    print("  papers          %d active, %d tombstoned" % (out["papers_active"], out["papers_tombstoned"]))
    print("  analysis        %d profiles, %d claims, %d relations, %d topics, overview=%s"
          % (out["profiles"], out["claims"], out["relations"], out["topics"], out["overview"]))
    print("  other files     %d unsupported, %d archives" % (out["unsupported_files"], out["archives"]))
    if out["pending_model_work"]:
        print("  pending         %s" % ", ".join(
            "%s: %s" % (k, "corpus" if v == ["*"] else "%d paper(s)" % len(v))
            for k, v in out["pending_model_work"].items()))
    else:
        print("  pending         nothing - all analysis stages current")
    if out["interrupted_stage"]:
        print("  ⚠ interrupted   %s" % out["interrupted_stage"])
    per_paper = [(r["paper_id"], (r.get("analysis_state") or {})) for r in rows]
    stale = [(pid, st) for pid, st in per_paper if "stale" in st.values()]
    if stale:
        print("  stale analyses:")
        for pid, st in stale:
            print("    %s  %s" % (pid, st))
    return 0


def cmd_tasks(args) -> int:
    stages = ["profile", "claims", "relations", "topics", "overview"] if args.stage == "all" else [args.stage]
    total = 0
    for stage in stages:
        orders = tasksmod.emit(args.kb_dir, stage, paper_ids=args.paper, limit=args.limit,
                               max_chars=args.max_chars, force=args.force)
        total += len(orders)
        for order in orders:
            log.info("work order: %s", order["order_path"])
            log.info("   answer  -> %s", order["response_path"])
    if not total:
        log.info("nothing pending for stage(s) %s (use --force --paper <id> to regenerate anyway)",
                 ", ".join(stages))
    else:
        log.info("%d work order(s). Read each .order.md, write the JSON answer to the response path, "
                 "then run `kb ingest`.", total)
    return 0


def cmd_ingest(args) -> int:
    kb_dir = args.kb_dir
    stage = args.stage
    paths = []
    if args.file:
        paths = [(args.paper[0] if args.paper else None, args.file)]
    else:
        directory = os.path.join(kb_dir, "tasks", stage)
        if not os.path.isdir(directory):
            raise SystemExit("no work orders for stage %r in %s" % (stage, directory))
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".response.json"):
                continue
            pid = name[: -len(".response.json")]
            if args.paper and pid not in args.paper:
                continue
            paths.append((None if pid == stage else pid, os.path.join(directory, name)))
    if not paths:
        raise SystemExit("no response files found. Write the model's answer to the response path "
                         "named in the .order.md first.")
    failed = 0
    for pid, path in paths:
        errors, warnings = ingestmod.ingest_file(kb_dir, stage, path, pid, args.model_id,
                                                 reviewed=args.reviewed)
        label = pid or stage
        for warning in warnings:
            log.warn("%s: %s", label, warning)
        if errors:
            failed += 1
            log.error("%s: %d problem(s) - NOTHING was written for it", label, len(errors))
            for err in errors[:20]:
                log.error("   %s", err)
        else:
            log.info("ingested %s from %s", label, os.path.relpath(path, kb_dir))
    if not args.no_refresh:
        report = buildmod.build(_paper_dir(kb_dir), kb_dir)
        log.info("refreshed index and rendered views (%d papers)", len(report.get("unchanged") or []) +
                 len(report.get("added") or []))
    return 1 if failed else 0


def cmd_search(args) -> int:
    conn = indexdb.connect(args.kb_dir, create=False)
    cfg = cfgmod.load(args.kb_dir)
    filters = _filters_from_args(args)
    query = indexdb.fts_query(args.query, raw=args.raw)
    if not args.raw:
        extra = indexdb.expand_synonyms(conn, args.query, cfg)
        if extra:
            query += " OR " + " OR ".join('"%s"' % e for e in extra)
            log.debug("synonym expansion: %s", extra)
    results = {}
    kinds = ["units", "claims", "papers", "topics"] if args.kind == "all" else [args.kind]
    if "units" in kinds:
        results["units"] = searchmod.search_units(conn, query, args.limit, filters)
    if "claims" in kinds:
        results["claims"] = searchmod.search_claims(conn, query, args.limit, filters)
    if "papers" in kinds:
        results["papers"] = searchmod.search_papers(conn, query, args.limit, filters)
    if "topics" in kinds:
        results["topics"] = searchmod.search_topics(conn, query, args.limit)
    conn.close()
    if args.json:
        print(json.dumps(results, indent=2, default=str))
        return 0
    print("query: %s   filters: %s" % (query[:160], filters.describe()))
    for kind, rows in results.items():
        print("\n== %s (%d) ==" % (kind, len(rows)))
        for row in rows:
            if kind == "units":
                print("  [%s %s p.%s §%s] score=%.2f" % (
                    row["paper_id"], row["unit_id"], row["page_label_start"],
                    (row["section_path"] or "")[:40], row["score"]))
                print("      %s" % row["text"][:220].replace("\n", " "))
            elif kind == "claims":
                print("  %s (%s, %s) score=%.2f" % (row["claim_id"], row["claim_type"],
                                                    row["polarity"], row["score"]))
                print("      %s" % row["statement"][:220])
            elif kind == "papers":
                print("  %s (%s) score=%.2f — %s" % (row["paper_id"], row["year"], row["score"],
                                                     (row["title"] or "")[:80]))
            else:
                print("  %s — %s score=%.2f" % (row["topic_id"], row["name"], row["score"]))
    return 0


def cmd_context(args) -> int:
    cfg = cfgmod.load(args.kb_dir)
    text, strategy = ctxmod.build_context(args.kb_dir, args.question, cfg, budget_tokens=args.budget,
                                          filters=_filters_from_args(args), raw_query=args.raw)
    if args.json:
        print(json.dumps({"pack": text, "strategy": strategy}, indent=2))
    else:
        print(text)
    if args.out:
        fsutil.atomic_write_text(args.out, text)
        log.info("written to %s", args.out)
    return 0


def cmd_show_paper(args) -> int:
    conn = indexdb.connect(args.kb_dir, create=False)
    matches = searchmod.resolve_paper(conn, args.paper_id)
    conn.close()
    if not matches:
        raise SystemExit("no paper matches %r (try `kb search %s \"%s\" --kind papers`)"
                         % (args.paper_id, args.kb_dir, args.paper_id))
    if len(matches) > 1:
        log.warn("%r matches %d papers: %s", args.paper_id, len(matches), ", ".join(matches))
    pid = matches[0]
    if args.json:
        rows = {r["paper_id"]: r for r in store.read_corpus_manifest(args.kb_dir)}
        print(json.dumps({
            "record": rows.get(pid),
            "profile": store.read_profile(args.kb_dir, pid),
            "claims": store.read_claims(args.kb_dir, pid),
            "relations": [r for r in store.read_relations(args.kb_dir)
                          if pid in (r.get("source_paper_id"), r.get("target_paper_id"))],
        }, indent=2, default=str))
        return 0
    path = store.paper_md_path(args.kb_dir, pid)
    if not os.path.exists(path):
        raise SystemExit("no rendered profile for %s (run `kb update`)" % pid)
    print(fsutil.read_text(path))
    return 0


def cmd_show_claim(args) -> int:
    kb_dir = args.kb_dir
    conn = indexdb.connect(kb_dir, create=False)
    row = conn.execute("SELECT * FROM claims WHERE claim_id=?", (args.claim_id,)).fetchone()
    if row is None:
        rows = conn.execute("SELECT claim_id FROM claims WHERE claim_id LIKE ?",
                            (args.claim_id + "%",)).fetchall()
        conn.close()
        raise SystemExit("claim %r not found%s" % (
            args.claim_id, ("; did you mean %s?" % ", ".join(r["claim_id"] for r in rows[:5])) if rows else ""))
    claim = dict(row)
    claim["support"] = json.loads(claim["support"] or "[]")
    relations = searchmod.relations_for(conn, [], [claim["claim_id"]])
    conn.close()
    if args.json:
        print(json.dumps({"claim": claim, "relations": relations}, indent=2, default=str))
        return 0
    print("claim_id: %s\npaper_id: %s\n" % (claim["claim_id"], claim["paper_id"]))
    print(claim["statement"] + "\n")
    for key in ("claim_type", "evidence_status", "polarity", "evidence_type", "subject",
                "intervention", "comparator", "outcome", "direction", "magnitude", "uncertainty",
                "conditions", "qualifiers", "section_origin", "confidence", "duplicate_of"):
        if claim.get(key) not in (None, "", 0):
            print("  %-18s %s" % (key, claim[key]))
    print("  %-18s %s" % ("primary evidence", bool(claim["is_primary_evidence"])))
    for loc in claim["support"]:
        print("\n  source %s" % render.cite(loc))
        print("  excerpt: %s" % (loc.get("excerpt") or "")[:300])
        passage = locators.quote(kb_dir, claim["paper_id"], loc)
        if passage:
            print("  passage: …%s…" % passage.replace("\n", " ")[:500])
    if relations:
        print("\n  related claims:")
        for rel in relations:
            print("    %s %s -> %s (%s)" % (rel["relation"], rel.get("source_claim_id"),
                                            rel.get("target_claim_id"), rel.get("rationale", "")[:80]))
    print("\n  full paper profile: papers/%s/paper.md ; text: docs/%s/text.txt"
          % (claim["paper_id"], claim["paper_id"]))
    return 0


def cmd_validate(args) -> int:
    result = validatemod.validate(args.kb_dir, deep=not args.fast)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("validation: %s" % ("PASS" if result["ok"] else "FAIL"))
        for key, value in sorted(result["stats"].items()):
            print("  %-20s %s" % (key, value))
        for label in ("errors", "warnings"):
            items = result[label]
            print("  %s: %d" % (label, len(items)))
            for item in items[: (None if args.all else 15)]:
                print("    [%s] %s: %s" % (item["kind"], item["where"], item["message"]))
            if not args.all and len(items) > 15:
                print("    … %d more (see reports/validation_report.md)" % (len(items) - 15))
    return 0 if result["ok"] else 2


def cmd_reindex(args) -> int:
    report = buildmod.build(_paper_dir(args.kb_dir), args.kb_dir, reindex_only=True)
    log.info("reindexed: %s", ", ".join("%s=%d" % kv for kv in sorted((report["counts"] or {}).items())))
    return 0


def cmd_eval(args) -> int:
    result = evaluate.run(args.kb_dir, args.questions, budget=args.budget)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if result["passed"] else 2
    print("evaluation: %d/%d questions passed" % (result["n_passed"], result["n_questions"]))
    for row in result["questions"]:
        print("\n%s [%s]" % (row["id"], "PASS" if row["passed"] else "FAIL"))
        print("  question: %s" % row["question"])
        for key in ("expected_papers_found", "expected_claims_found", "citation_coverage",
                    "locator_validity", "counterevidence_present", "tokens_used"):
            if key in row:
                print("  %-24s %s" % (key, row[key]))
        for note in row.get("notes") or []:
            print("  note: %s" % note)
    print("\nreport: %s" % result["report_path"])
    return 0 if result["passed"] else 2


# ---------------------------------------------------------------- parser
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="kb", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version",
                        version="paperbase schema %s / pipeline %s" % (SCHEMA_VERSION, PIPELINE_VERSION))
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("build", help="parse a paper directory into a new KB")
    p.add_argument("paper_dir")
    p.add_argument("kb_dir")
    p.add_argument("--force", action="store_true", help="re-parse everything, ignoring fingerprints")
    p.add_argument("--copy-sources", action="store_true",
                   help="also copy source documents into <kb>/sources/ (originals untouched)")
    _add_common(p)
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("update", help="incremental update of an existing KB")
    p.add_argument("kb_dir")
    p.add_argument("paper_dir", nargs="?", help="defaults to the directory recorded in the manifest")
    p.add_argument("--force", action="store_true")
    _add_common(p)
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("status", help="show what exists, what is stale and what is pending")
    p.add_argument("kb_dir")
    p.add_argument("--json", action="store_true")
    _add_common(p)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("tasks", help="emit work orders for model-dependent stages")
    p.add_argument("kb_dir")
    p.add_argument("--stage", default="all",
                   choices=["profile", "claims", "relations", "topics", "overview", "all"])
    p.add_argument("--paper", action="append", help="restrict to these paper ids (repeatable)")
    p.add_argument("--limit", type=int)
    p.add_argument("--max-chars", type=int, default=140000,
                   help="cap the source text embedded in one work order")
    p.add_argument("--force", action="store_true", help="emit even if the stage is current")
    _add_common(p)
    p.set_defaults(func=cmd_tasks)

    p = sub.add_parser("ingest", help="validate and install a model's answer")
    p.add_argument("kb_dir")
    p.add_argument("--stage", required=True,
                   choices=["profile", "claims", "relations", "topics", "overview", "terminology"])
    p.add_argument("--paper", action="append")
    p.add_argument("--file", help="explicit response file (default: all responses in tasks/<stage>/)")
    p.add_argument("--model-id", default=None, help="model that produced the answer (recorded in provenance)")
    p.add_argument("--reviewed", action="store_true", help="mark as human-reviewed")
    p.add_argument("--no-refresh", action="store_true", help="skip reindex/render afterwards")
    _add_common(p)
    p.set_defaults(func=cmd_ingest)

    for name, helptext in (("search", "lexical search over units/claims/papers/topics"),):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("kb_dir")
        p.add_argument("query")
        p.add_argument("--kind", default="all", choices=["units", "claims", "papers", "topics", "all"])
        p.add_argument("--limit", type=int, default=10)
        p.add_argument("--raw", action="store_true", help="pass the query straight to FTS5")
        p.add_argument("--json", action="store_true")
        _add_filters(p)
        _add_common(p)
        p.set_defaults(func=cmd_search)

    p = sub.add_parser("context", help="build a compact evidence pack for another agent")
    p.add_argument("kb_dir")
    p.add_argument("question")
    p.add_argument("--budget", type=int, help="token budget (default from config)")
    p.add_argument("--out", help="also write the pack to this file")
    p.add_argument("--raw", action="store_true")
    p.add_argument("--json", action="store_true")
    _add_filters(p)
    _add_common(p)
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("show-paper", help="print a paper's profile (id, id prefix, DOI or title)")
    p.add_argument("kb_dir")
    p.add_argument("paper_id")
    p.add_argument("--json", action="store_true")
    _add_common(p)
    p.set_defaults(func=cmd_show_paper)

    p = sub.add_parser("show-claim", help="print a claim with its exact source passage")
    p.add_argument("kb_dir")
    p.add_argument("claim_id")
    p.add_argument("--json", action="store_true")
    _add_common(p)
    p.set_defaults(func=cmd_show_claim)

    p = sub.add_parser("validate", help="schema, provenance, locator and link validation")
    p.add_argument("kb_dir")
    p.add_argument("--fast", action="store_true", help="skip excerpt verification (no text reads)")
    p.add_argument("--all", action="store_true", help="print every issue")
    p.add_argument("--json", action="store_true")
    _add_common(p)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("reindex", help="rebuild the derived SQLite index and rendered views")
    p.add_argument("kb_dir")
    _add_common(p)
    p.set_defaults(func=cmd_reindex)

    p = sub.add_parser("eval", help="run retrieval evaluation questions")
    p.add_argument("kb_dir")
    p.add_argument("questions", nargs="?", help="YAML/JSON question set (default: <kb>/eval/questions.yaml)")
    p.add_argument("--budget", type=int, default=6000)
    p.add_argument("--json", action="store_true")
    _add_common(p)
    p.set_defaults(func=cmd_eval)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    log.set_level(getattr(args, "quiet", False), getattr(args, "verbose", False))
    if getattr(args, "model_id", "sentinel") is None:
        cfg = cfgmod.load(args.kb_dir)
        args.model_id = cfg["analysis"]["model_id"]
        if args.model_id == "unspecified":
            log.warn("no --model-id given and analysis.model_id is 'unspecified'; provenance will "
                     "record 'unspecified'. Set it so a model change invalidates analyses.")
    try:
        return args.func(args)
    except KeyboardInterrupt:
        log.error("interrupted - canonical artifacts are written atomically, so the KB is intact. "
                  "Re-run the same command to continue.")
        return 130
    except SystemExit:
        raise
    except Exception as exc:  # actionable failure, not a bare traceback
        if getattr(args, "verbose", False):
            raise
        log.error("%s: %s", type(exc).__name__, exc)
        log.error("re-run with -v for a full traceback")
        return 1


def _add_filters(p: argparse.ArgumentParser) -> None:
    p.add_argument("--paper", action="append", help="restrict to paper id (repeatable)")
    p.add_argument("--exclude-paper", action="append")
    p.add_argument("--year", action="append")
    p.add_argument("--type", action="append", help="document type, e.g. review, research_article")
    p.add_argument("--method", action="append")
    p.add_argument("--dataset", action="append")
    p.add_argument("--concept", action="append")
    p.add_argument("--author", action="append")
    p.add_argument("--claim-type", action="append", dest="claim_type")
    p.add_argument("--min-confidence", type=float)
    p.add_argument("--primary-only", action="store_true",
                   help="only claims that are the paper's own primary evidence")


if __name__ == "__main__":
    sys.exit(main())
