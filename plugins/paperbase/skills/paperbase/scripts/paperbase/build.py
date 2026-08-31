"""Deterministic build / incremental update.

This module NEVER calls a language model.  It parses, normalizes, extracts
metadata, maintains identity and fingerprints, applies human overrides, links
document families, rebuilds the search index and renders navigation - then
reports which model-dependent stages are pending (see tasks.py).

Incremental behaviour:
  * unchanged content hash + unchanged fingerprints  -> nothing is re-parsed
  * same content, new path                           -> rename, id preserved
  * changed content                                  -> reparse; downstream
    paper stages and the corpus stages that consumed it are marked stale
  * removed file                                     -> tombstone, artifacts
    deleted, dependent relations retired, corpus stages marked stale
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from . import (bootstrap, config as cfgmod, extract, fsutil, ids, indexdb, log,
               manifest, metadata as metamod, overrides as ovmod, parsers, render,
               scan, store, tune)


def _parse_document(path: str, cfg: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Try adapters in order; return (parse_result, document)."""
    last_error = None
    for adapter in parsers.select(path, cfg):
        try:
            parse_result = adapter.parse(path, cfg)
        except Exception as exc:
            last_error = "%s: %s" % (adapter.NAME, exc)
            log.debug("adapter %s failed on %s: %s", adapter.NAME, os.path.basename(path), exc)
            continue
        doc = extract.build_document(parse_result, cfg)
        if doc["report"]["text_quality"] != "empty":
            return parse_result, doc
        last_error = "%s produced no usable text" % adapter.NAME
        # keep the empty result from the first adapter as the honest outcome
        fallback = (parse_result, doc)
        if adapter is parsers.select(path, cfg)[-1]:
            return fallback
    if last_error:
        # every adapter failed hard
        raise RuntimeError(last_error)
    raise RuntimeError("no adapter produced output for %s" % os.path.basename(path))


def _paper_record(pid: str, basis: str, key: str, doc_meta: Dict[str, Any], entry: Dict[str, Any],
                  parse_report: Dict[str, Any], source: Dict[str, Any], paper_dir: str) -> Dict[str, Any]:
    return {
        "paper_id": pid,
        "identity_basis": basis,
        "identity_key": key,
        "status": "active",
        "source_path": source["path"],
        "source_dir": paper_dir,
        "content_sha256": source["content_sha256"],
        "size_bytes": source["size_bytes"],
        "metadata": doc_meta["metadata"],
        "metadata_provenance": doc_meta["metadata_provenance"],
        "parse": {
            "adapter": parse_report["adapter"],
            "adapter_version": parse_report["adapter_version"],
            "doc_kind": parse_report["doc_kind"],
            "n_pages": parse_report.get("n_pages"),
            "n_units": parse_report["n_units"],
            "n_chars": parse_report["n_chars"],
            "text_quality": parse_report["text_quality"],
            "needs_ocr": parse_report["needs_ocr"],
            "layout_signal": parse_report["layout_signal"],
            "sections": parse_report["sections"],
            "warnings": parse_report["warnings"],
        },
    }


def build(paper_dir: str, kb_dir: str, force: bool = False, copy_sources: Optional[bool] = None,
          reindex_only: bool = False, no_install: bool = False, no_tune: bool = False,
          _after_tuning: bool = False) -> Dict[str, Any]:
    started = time.time()
    paper_dir = os.path.abspath(os.path.expanduser(paper_dir))
    kb_dir = os.path.abspath(os.path.expanduser(kb_dir))
    if os.path.abspath(kb_dir).startswith(os.path.abspath(paper_dir) + os.sep):
        raise SystemExit(
            "refusing to build: the KB directory (%s) is inside the paper directory (%s).\n"
            "The paper directory must never be written to. Choose a KB path outside it."
            % (kb_dir, paper_dir))

    store.init(kb_dir)
    cfgmod.install(kb_dir)
    ovmod.install_template(kb_dir)
    cfg = cfgmod.load(kb_dir)
    if copy_sources is not None:
        cfg["ingestion"]["copy_sources"] = copy_sources

    man = manifest.load(kb_dir) or manifest.empty(paper_dir, cfg)
    previous_paper_dir = man.get("paper_dir")
    man["paper_dir"] = paper_dir
    man["config_hash"] = cfgmod.config_hash(cfg)
    is_new = not man["papers"]

    scanned = scan.scan(paper_dir, cfg)
    if not _after_tuning:
        setup = bootstrap.ensure_for_corpus(
            sorted({d["ext"] for d in scanned["documents"]}), no_install=no_install)
        for capability, outcome in (setup.get("capabilities") or {}).items():
            if outcome["installed"]:
                log.audit(kb_dir, "dependency_installed", capability=capability,
                          packages=outcome["installed"], target=bootstrap.libdir())
            for action in outcome["actions"]:
                log.warn("%s", action)
        man["environment"] = {
            "python": sys.version.split()[0],
            "capabilities": {c: o["satisfied_by"]
                             for c, o in (setup.get("capabilities") or {}).items()},
            "private_libdir": bootstrap.libdir(),
        }
    man["archives"] = scanned["archives"]
    man["unsupported"] = scanned["unsupported"]

    report: Dict[str, Any] = {
        "kb_dir": kb_dir, "paper_dir": paper_dir, "mode": "build" if is_new else "update",
        "added": [], "modified": [], "renamed": [], "unchanged": [], "removed": [],
        "reparsed": [], "failed": [], "id_changed": [], "warnings": [],
        "stages_run": [], "model_work_pending": {}, "counts": {},
    }
    if previous_paper_dir and previous_paper_dir != paper_dir:
        report["warnings"].append(
            "paper directory changed from %s to %s; source paths were re-based"
            % (previous_paper_dir, paper_dir))

    by_hash: Dict[str, str] = {
        e["content_sha256"]: pid for pid, e in man["papers"].items()
        if e.get("status") == "active" and e.get("content_sha256")
    }
    by_path: Dict[str, str] = {
        e["source_path"]: pid for pid, e in man["papers"].items()
        if e.get("status") == "active" and e.get("source_path")
    }
    seen_ids: set = set()
    records: Dict[str, Dict[str, Any]] = {}
    hash_seen: Dict[str, str] = {}  # content hash -> paper_id, within this scan

    fsutil.journal_begin(kb_dir, "parse", [s["path"] for s in scanned["documents"]])
    for source in scanned["documents"]:
        if source["content_sha256"] in hash_seen:
            # byte-identical to a file already ingested in this run: the same document
            # under two names. Keep one record and disclose both paths.
            twin = hash_seen[source["content_sha256"]]
            records[twin].setdefault("duplicate_source_paths", []).append(source["path"])
            report.setdefault("duplicate_files", []).append(
                {"paper_id": twin, "path": source["path"],
                 "same_as": records[twin]["source_path"]})
            report["warnings"].append(
                "%s is byte-identical to %s; both paths are recorded under paper %s (no knowledge "
                "was duplicated)" % (source["path"], records[twin]["source_path"], twin))
            continue
        pid_by_hash = by_hash.get(source["content_sha256"])
        pid_by_path = by_path.get(source["path"])
        existing_id = pid_by_hash or pid_by_path
        entry = man["papers"].get(existing_id) if existing_id else None

        change = "added"
        if entry and pid_by_hash and entry["source_path"] != source["path"]:
            change = "renamed"
        elif entry and pid_by_hash:
            change = "unchanged"
        elif entry:
            change = "modified"

        # Does anything require re-parsing?
        adapter_probe = parsers.select(source["abspath"], cfg)[0]
        expected_parse_fp = manifest.paper_fingerprint(
            "parse", cfg, source["content_sha256"], adapter_probe.NAME, adapter_probe.VERSION, {})
        stale_parse = force or not entry or manifest.is_stale(man, existing_id, "parse", expected_parse_fp)
        if not stale_parse and reindex_only:
            stale_parse = False

        if not stale_parse:
            record = dict(entry)
            record["source_path"] = source["path"]
            record["status"] = "active"
            records[existing_id] = record
            hash_seen[source["content_sha256"]] = existing_id
            seen_ids.add(existing_id)
            report[change if change in ("unchanged", "renamed") else "unchanged"].append(
                {"paper_id": existing_id, "path": source["path"]})
            continue

        try:
            parse_result, doc = _parse_document(source["abspath"], cfg)
        except Exception as exc:
            report["failed"].append({"path": source["path"], "error": str(exc)})
            log.warn("%s could not be parsed: %s", source["path"], exc)
            continue

        doc_meta = metamod.extract_metadata(doc, parse_result, source["abspath"], cfg)
        new_pid, basis, key = ids.paper_id(doc_meta["metadata"], source["content_sha256"])
        if new_pid in records:
            # two files claiming the same identity: keep both, disambiguate by content
            new_pid = "%s-%s" % (new_pid, source["content_sha256"][:4])
            report["warnings"].append(
                "two documents resolved to the same identity; %s was given a content-suffixed id "
                "and a possible-duplicate relation - resolve deliberately in overrides.yaml"
                % source["path"])
        if entry and existing_id and new_pid != existing_id:
            manifest.tombstone(man, existing_id, "identity changed after re-parse (now %s)" % new_pid)
            store.remove_paper_artifacts(kb_dir, existing_id)
            report["id_changed"].append({"from": existing_id, "to": new_pid, "path": source["path"]})

        parse_report = doc["report"]
        outputs = store.write_document(kb_dir, new_pid, doc, {
            "paper_id": new_pid, "source_path": source["path"],
            "content_sha256": source["content_sha256"],
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        })
        if cfg["ingestion"]["copy_sources"]:
            dest = os.path.join(kb_dir, "sources", source["path"])
            fsutil.ensure_dir(os.path.dirname(dest))
            shutil.copy2(source["abspath"], dest)

        record = _paper_record(new_pid, basis, key, doc_meta, entry or {}, parse_report, source, paper_dir)
        records[new_pid] = record
        hash_seen[source["content_sha256"]] = new_pid
        seen_ids.add(new_pid)
        prev_stages = (entry or {}).get("stages", {}) if new_pid == existing_id else {}
        man["papers"][new_pid] = dict(record, stages=dict(prev_stages))
        manifest.record_paper_stage(
            man, new_pid, "parse", expected_parse_fp, outputs,
            adapter=parse_report["adapter"], adapter_version=parse_report["adapter_version"],
            text_quality=parse_report["text_quality"], n_warnings=len(parse_report["warnings"]))
        units_fp = manifest.paper_fingerprint("units", cfg, source["content_sha256"], "", "",
                                              {"parse": expected_parse_fp})
        manifest.record_paper_stage(man, new_pid, "units", units_fp,
                                    [store.units_path(kb_dir, new_pid)], n_units=parse_report["n_units"])
        meta_fp = manifest.paper_fingerprint("metadata", cfg, source["content_sha256"], "", "",
                                             {"parse": expected_parse_fp})
        manifest.record_paper_stage(man, new_pid, "metadata", meta_fp, [store.parse_path(kb_dir, new_pid)],
                                    identity_basis=basis)
        report["reparsed"].append({"paper_id": new_pid, "path": source["path"]})
        report[change].append({"paper_id": new_pid, "path": source["path"]})
        if parse_report["text_quality"] in ("poor", "empty"):
            report["warnings"].append("%s: text quality %s - %s" % (
                source["path"], parse_report["text_quality"], parse_report["warnings"][0] if parse_report["warnings"] else ""))
    fsutil.journal_end(kb_dir)

    # ---------------------------------------------------------------- removals
    for pid, entry in list(man["papers"].items()):
        if entry.get("status") != "active" or pid in seen_ids:
            continue
        manifest.tombstone(man, pid, "source document no longer present in the paper directory")
        removed_paths = store.remove_paper_artifacts(kb_dir, pid)
        report["removed"].append({"paper_id": pid, "path": entry.get("source_path"),
                                  "artifacts_removed": len(removed_paths)})

    for pid in seen_ids:
        man["papers"][pid].update({k: v for k, v in records[pid].items() if k != "stages"})
        man["papers"][pid]["status"] = "active"

    # ---------------------------------------------------------------- tuning
    tuning_state: Dict[str, Any] = {"ran": False, "reparsed_for_headings": False}
    if cfg["tuning"]["enabled"] and not no_tune and not reindex_only:
        expected_tuning = manifest.corpus_fingerprint("tuning", cfg, man)
        if force or manifest.corpus_stale(man, "tuning", expected_tuning):
            active_now = manifest.active_papers(man)
            mined = tune.mine(kb_dir, cfg, active_now)
            path, parsing_changed, _prev = tune.write_derived(kb_dir, mined)
            report_path = tune.report(kb_dir, mined, parsing_changed)
            tuning_state = {
                "ran": True, "derived_config": path, "report": report_path,
                "acronyms": len(mined["acronyms"]), "variants": len(mined["variants"]),
                "priority_terms": len(mined["priority_terms"]),
                "section_headings": [h["heading"] for h in mined["section_headings"]],
                "reparsed_for_headings": False,
            }
            report["stages_run"].append("tuning")
            log.audit(kb_dir, "tuning", **{k: v for k, v in tuning_state.items()
                                           if k in ("acronyms", "variants", "priority_terms")})
            cfg = cfgmod.load(kb_dir)  # pick up derived.yaml
            manifest.record_corpus_stage(
                man, "tuning", manifest.corpus_fingerprint("tuning", cfg, man), [path, report_path],
                n_acronyms=len(mined["acronyms"]), n_terms=len(mined["priority_terms"]))
            manifest.save(kb_dir, man)
            if parsing_changed and not _after_tuning:
                # Adopting corpus-specific section headings changes segmentation, so the
                # corpus is re-parsed exactly once; the new parsing config makes every
                # parse fingerprint stale, which is what drives the re-parse.
                log.info("adopted %d corpus-specific section heading(s); re-parsing once so "
                         "section paths reflect them", len(mined["section_headings"]))
                second = build(paper_dir, kb_dir, force=False, copy_sources=copy_sources,
                               no_install=True, no_tune=True, _after_tuning=True)
                second["tuning"] = dict(tuning_state, reparsed_for_headings=True)
                second["mode"] = report["mode"]
                second["elapsed_s"] = round(time.time() - started, 2)
                render.write_update_report(kb_dir, second)
                return second
    report["tuning"] = tuning_state

    # ---------------------------------------------------------------- overrides
    ov, ov_problems = ovmod.load(kb_dir)
    for problem in ov_problems:
        report["warnings"].append("overrides: %s" % problem)
    active = manifest.active_papers(man)
    paper_rows: List[Dict[str, Any]] = []
    for pid in active:
        record = dict(man["papers"][pid])
        record.pop("stages", None)
        record = ovmod.apply_metadata(record, ov)
        stages = man["papers"][pid].get("stages", {})
        record["has_profile"] = os.path.exists(store.profile_path(kb_dir, pid))
        record["analysis_state"] = manifest.analysis_state(man, cfg, pid)
        if record.get("status") == "excluded_by_override":
            man["papers"][pid]["status"] = "excluded"
            continue
        paper_rows.append(record)
    active = [r["paper_id"] for r in paper_rows]

    # ---------------------------------------------------------------- families
    families = metamod.detect_families(paper_rows, cfg)
    families, applied_families = ovmod.apply_families(families, ov)
    fsutil.atomic_write_jsonl(store.families_path(kb_dir), families)
    fam_fp = manifest.corpus_fingerprint("families", cfg, man)
    manifest.record_corpus_stage(man, "families", fam_fp, [store.families_path(kb_dir)],
                                 n_relations=len(families))
    report["stages_run"].append("families")

    for row in paper_rows:
        fam = [f for f in families if f["source_paper_id"] == row["paper_id"] and f["target_paper_id"]]
        if fam:
            row["family"] = [{"relation": f["relation"], "target_paper_id": f["target_paper_id"],
                              "confidence": f["confidence"], "origin": f["origin"]} for f in fam]

    # ------------------------------------------------- retire dangling relations
    relations = fsutil.read_jsonl(store.relations_path(kb_dir))
    live = set(active)
    kept, retired = [], []
    for rel in relations:
        if rel["source_paper_id"] not in live or (rel.get("target_paper_id") and rel["target_paper_id"] not in live):
            rel["retired_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
            rel["retired_reason"] = "a referenced paper left the corpus"
            retired.append(rel)
        else:
            kept.append(rel)
    if retired:
        existing = fsutil.read_jsonl(store.tombstoned_relations_path(kb_dir))
        fsutil.atomic_write_jsonl(store.tombstoned_relations_path(kb_dir), existing + retired)
        report["warnings"].append("%d relation(s) retired because a referenced paper left the corpus" % len(retired))
    kept, applied_relations = ovmod.apply_relations(kept, ov)
    fsutil.atomic_write_jsonl(store.relations_path(kb_dir), kept)

    # ---------------------------------------------------------------- claims + overrides
    all_claims: List[Dict[str, Any]] = []
    applied_claims: List[str] = []
    for pid in active:
        claims = store.read_claims(kb_dir, pid)
        if not claims:
            continue
        claims, applied = ovmod.apply_claims(claims, ov)
        if applied:
            fsutil.atomic_write_jsonl(store.claims_path(kb_dir, pid), claims)
            applied_claims.extend(applied)
        all_claims.extend(claims)

    # ------------------------------------------------- stale synthesis detection
    stale_corpus = []
    for stage in ("relations", "topics", "overview"):
        expected = manifest.corpus_fingerprint(stage, cfg, man)
        if manifest.corpus_stale(man, stage, expected):
            stale_corpus.append(stage)
    report["stale_corpus_stages"] = stale_corpus

    topics = store.read_topics(kb_dir)
    live_claim_ids = {c["claim_id"] for c in all_claims}
    for topic in topics:
        missing = _missing_claim_refs(topic, live_claim_ids)
        path = os.path.join(store.topics_dir(kb_dir), topic["topic_id"] + ".json")
        if missing:
            topic.setdefault("warnings", []).append(
                "STALE: %d referenced claim id(s) no longer exist (papers changed or were removed); "
                "regenerate this topic synthesis" % len(missing))
            fsutil.atomic_write_json(path, topic)
            report["warnings"].append("topic %s references %d missing claim(s) - regenerate it"
                                      % (topic["topic_id"], len(missing)))

    # ---------------------------------------------------------------- corpus manifest
    store.write_corpus_manifest(kb_dir, paper_rows)

    # ---------------------------------------------------------------- index
    units_by_paper = {pid: store.read_units(kb_dir, pid) for pid in active}
    for row in paper_rows:
        profile = store.read_profile(kb_dir, row["paper_id"])
        if profile:
            row["retrieval_terms"] = (profile.get("analysis") or {}).get("retrieval_terms") or []
    terminology = store.read_terminology(kb_dir).get("terms") or []
    index_fp = manifest.corpus_fingerprint("index", cfg, man)
    counts = indexdb.rebuild(kb_dir, paper_rows, units_by_paper, all_claims,
                             kept + families, topics, terminology, index_fp)
    manifest.record_corpus_stage(man, "index", index_fp, [indexdb.db_path(kb_dir)], counts=counts)
    report["stages_run"].append("index")
    report["counts"] = counts

    # ---------------------------------------------------------------- unapplied overrides
    unapplied = ovmod.unapplied(ov, active, sorted(live_claim_ids), applied_claims,
                               applied_relations, applied_families)
    report["unapplied_overrides"] = unapplied
    for item in unapplied:
        report["warnings"].append("override not applied - %s" % item)
    report["overrides_applied"] = {
        "papers": len([p for p in (ov.get("papers") or {}) if p in active]),
        "claims": len(applied_claims),
        "relations": len(applied_relations),
        "families": len(applied_families),
    }

    # ---------------------------------------------------------------- render + save
    report["model_work_pending"] = manifest.model_work_pending(man, cfg)
    manifest.save(kb_dir, man)
    render.render_all(kb_dir, cfg, man, paper_rows, all_claims, kept + families, topics,
                      store.read_overview(kb_dir), terminology)
    report["elapsed_s"] = round(time.time() - started, 2)
    render.write_update_report(kb_dir, report)
    log.audit(kb_dir, "build" if is_new else "update",
              added=len(report["added"]), modified=len(report["modified"]),
              renamed=len(report["renamed"]), removed=len(report["removed"]),
              unchanged=len(report["unchanged"]), reparsed=len(report["reparsed"]),
              failed=len(report["failed"]), counts=counts,
              pipeline=man["pipeline_version"], schema=man["schema_version"],
              config_hash=man["config_hash"], elapsed_s=report["elapsed_s"])
    return report




def _missing_claim_refs(node: Any, live: set) -> List[str]:
    missing: List[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "claim_ids" and isinstance(value, list):
                missing.extend([c for c in value if c not in live])
            else:
                missing.extend(_missing_claim_refs(value, live))
    elif isinstance(node, list):
        for item in node:
            missing.extend(_missing_claim_refs(item, live))
    return missing
