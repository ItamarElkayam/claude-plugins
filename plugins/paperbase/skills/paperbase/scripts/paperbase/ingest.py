"""Validate and install model-generated analysis into the KB.

Nothing reaches a canonical artifact without: schema validation, locator
resolution (which proves every excerpt exists in the normalized source text),
provenance stamping (model id, prompt, prompt version, timestamp) and a manifest
fingerprint so the result is recognised as current until an input changes.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from . import (SCHEMA_VERSION, config as cfgmod, fsutil, ids, jsonschema_lite,
               locators, log, manifest, store, tasks)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _stamp(obj: Dict[str, Any], stage: str, model_id: str, reviewed: bool = False) -> Dict[str, Any]:
    spec = tasks.STAGE_SPEC[stage]
    obj["generated_by"] = {
        "model_id": model_id,
        "prompt": spec["prompt"],
        "prompt_version": cfgmod.prompt_version(spec["prompt"]),
        "generated_at": _now(),
        "reviewed_by_human": bool(reviewed),
    }
    return obj


def _load_response(path: str) -> Any:
    if not os.path.exists(path):
        raise SystemExit("response file not found: %s\nWrite the model's answer there first "
                         "(see the matching .order.md)." % path)
    with open(path, "r", encoding="utf-8") as fh:
        body = fh.read().strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1]
        body = body.rsplit("```", 1)[0]
    try:
        return json.loads(body)
    except ValueError as exc:
        raise SystemExit("%s is not valid JSON: %s" % (path, exc))


def ingest_profile(kb_dir: str, paper_id: str, data: Dict[str, Any], model_id: str,
                   reviewed: bool = False) -> Tuple[List[str], List[str]]:
    cfg = cfgmod.load(kb_dir)
    errors: List[str] = []
    warnings: List[str] = []
    data = dict(data)
    data["paper_id"] = paper_id
    data["schema_version"] = SCHEMA_VERSION
    _stamp(data, "profile", model_id, reviewed)
    units = store.read_units(kb_dir, paper_id)
    text = store.read_text(kb_dir, paper_id)
    parse = store.read_parse(kb_dir, paper_id)
    method = parse.get("adapter", "text")
    source = os.path.basename(parse.get("source_path") or "")
    _resolve_locators(data, kb_dir, paper_id, units, text, source, method, warnings)
    errs = jsonschema_lite.validate(data, os.path.join(cfgmod.SCHEMAS_DIR, "paper_profile.schema.json"))
    if errs:
        return errs, warnings
    for key in ("limitations_inferred", "threats_to_validity"):
        for item in (data["analysis"].get(key) or []):
            if item.get("basis") == "agent_inferred" and not item.get("rationale"):
                errors.append("analysis.%s: an agent_inferred statement needs a rationale" % key)
    if errors:
        return errors, warnings
    fsutil.ensure_dir(store.paper_dir(kb_dir, paper_id))
    fsutil.atomic_write_json(store.profile_path(kb_dir, paper_id), data)
    _record(kb_dir, paper_id, "profile", cfg, model_id,
            outputs=[store.profile_path(kb_dir, paper_id)])
    log.audit(kb_dir, "ingest_profile", paper_id=paper_id, model_id=model_id,
              prompt_version=data["generated_by"]["prompt_version"], warnings=len(warnings))
    return [], warnings


def ingest_claims(kb_dir: str, paper_id: str, data: List[Dict[str, Any]], model_id: str,
                  reviewed: bool = False) -> Tuple[List[str], List[str]]:
    cfg = cfgmod.load(kb_dir)
    if isinstance(data, dict) and "claims" in data:
        data = data["claims"]
    if not isinstance(data, list):
        return ["expected a JSON array of claim objects"], []
    errors: List[str] = []
    warnings: List[str] = []
    units = store.read_units(kb_dir, paper_id)
    text = store.read_text(kb_dir, paper_id)
    parse = store.read_parse(kb_dir, paper_id)
    method = parse.get("adapter", "text")
    source = os.path.basename(parse.get("source_path") or "")
    by_unit = {u["unit_id"]: u for u in units}
    schema_path = os.path.join(cfgmod.SCHEMAS_DIR, "claim.schema.json")
    out: List[Dict[str, Any]] = []
    seen_statements: Dict[str, str] = {}
    for i, raw in enumerate(data, start=1):
        claim = dict(raw)
        claim["paper_id"] = paper_id
        claim.setdefault("claim_id", ids.claim_id(paper_id, claim.get("statement", ""), i))
        if not claim["claim_id"].startswith(paper_id):
            claim["claim_id"] = ids.claim_id(paper_id, claim.get("statement", ""), i)
        _stamp(claim, "claims", model_id, reviewed)
        support = claim.get("support") or []
        claim["support"] = [
            locators.resolve(kb_dir, paper_id, loc, units, text, source, method) for loc in support
        ]
        if not claim["support"]:
            errors.append("claim %d (%s): no source locator - claims must be traceable"
                          % (i, claim.get("statement", "")[:60]))
            continue
        for loc in claim["support"]:
            for warning in loc.get("warnings") or []:
                warnings.append("claim %s: %s" % (claim["claim_id"], warning))
        first_unit = by_unit.get((claim["support"][0] or {}).get("unit_id"))
        if first_unit and not claim.get("section_origin"):
            claim["section_origin"] = " > ".join(first_unit.get("section_path") or []) or None
        if claim.get("evidence_status") in ("calculated", "inferred") and not claim.get("derivation"):
            errors.append("claim %s: evidence_status=%s requires a derivation"
                          % (claim["claim_id"], claim["evidence_status"]))
        norm = ids.normalize_title(claim.get("statement", ""))
        if norm in seen_statements:
            claim["duplicate_of"] = seen_statements[norm]
            warnings.append("claim %s duplicates %s (kept, marked duplicate_of)"
                            % (claim["claim_id"], seen_statements[norm]))
        else:
            seen_statements[norm] = claim["claim_id"]
        errs = jsonschema_lite.validate(claim, schema_path)
        if errs:
            errors.extend(["claim %s: %s" % (claim["claim_id"], e) for e in errs])
            continue
        out.append(claim)
    if errors:
        return errors, warnings
    limits = cfg["analysis"]
    if len(out) < limits["min_claims_per_paper"]:
        warnings.append("only %d claims extracted (analysis.min_claims_per_paper=%d): the paper may be "
                        "under-analysed or genuinely thin" % (len(out), limits["min_claims_per_paper"]))
    if len(out) > limits["max_claims_per_paper"]:
        warnings.append("%d claims exceeds analysis.max_claims_per_paper=%d" % (len(out), limits["max_claims_per_paper"]))
    fsutil.ensure_dir(store.paper_dir(kb_dir, paper_id))
    fsutil.atomic_write_jsonl(store.claims_path(kb_dir, paper_id), out)
    _record(kb_dir, paper_id, "claims", cfg, model_id, outputs=[store.claims_path(kb_dir, paper_id)],
            n_claims=len(out))
    log.audit(kb_dir, "ingest_claims", paper_id=paper_id, n_claims=len(out), model_id=model_id,
              warnings=len(warnings))
    return [], warnings


def ingest_relations(kb_dir: str, data: List[Dict[str, Any]], model_id: str,
                     reviewed: bool = False) -> Tuple[List[str], List[str]]:
    cfg = cfgmod.load(kb_dir)
    if isinstance(data, dict) and "relations" in data:
        data = data["relations"]
    if not isinstance(data, list):
        return ["expected a JSON array of relation objects"], []
    man = manifest.load(kb_dir) or {}
    active = set(manifest.active_papers(man))
    known_claims = set()
    for pid in active:
        known_claims.update(c["claim_id"] for c in store.read_claims(kb_dir, pid))
    schema_path = os.path.join(cfgmod.SCHEMAS_DIR, "relation.schema.json")
    errors, warnings, out = [], [], []
    for raw in data:
        rel = dict(raw)
        rel.setdefault("origin", "inferred_by_analysis")
        _stamp(rel, "relations", model_id, reviewed)
        rel["relation_id"] = rel.get("relation_id") or ids.relation_id(
            rel.get("source_paper_id", ""), rel.get("relation", ""),
            rel.get("target_paper_id") or "", rel.get("dimension") or "")
        for key in ("source_paper_id", "target_paper_id"):
            value = rel.get(key)
            if value and value not in active:
                errors.append("relation %s: %s %r is not an active paper" % (rel["relation_id"], key, value))
        for key in ("source_claim_id", "target_claim_id"):
            value = rel.get(key)
            if value and value not in known_claims:
                errors.append("relation %s: %s %r does not exist" % (rel["relation_id"], key, value))
        for side in ("evidence_source", "evidence_target"):
            pid = rel.get("source_paper_id" if side == "evidence_source" else "target_paper_id")
            if rel.get(side) and pid:
                rel[side] = [locators.resolve(kb_dir, pid, loc) for loc in rel[side]]
                for loc in rel[side]:
                    for warning in loc.get("warnings") or []:
                        warnings.append("relation %s: %s" % (rel["relation_id"], warning))
        if rel.get("relation") in ("contradicts", "partially-contradicts"):
            if not rel.get("difference_explanations"):
                errors.append("relation %s: a contradiction must list the alternative explanations "
                              "checked (population, task, data, metric, design, power...)" % rel["relation_id"])
            if not (rel.get("evidence_source") and rel.get("evidence_target")):
                warnings.append("relation %s: contradiction without evidence from both sides"
                                % rel["relation_id"])
        errs = jsonschema_lite.validate(rel, schema_path)
        if errs:
            errors.extend(["relation %s: %s" % (rel["relation_id"], e) for e in errs])
            continue
        out.append(rel)
    if errors:
        return errors, warnings
    existing = {r["relation_id"]: r for r in fsutil.read_jsonl(store.relations_path(kb_dir))}
    for rel in out:
        existing[rel["relation_id"]] = rel
    fsutil.atomic_write_jsonl(store.relations_path(kb_dir), list(existing.values()))
    man2 = manifest.load(kb_dir)
    fingerprint = manifest.corpus_fingerprint("relations", cfgmod.load(kb_dir), man2)
    manifest.record_corpus_stage(man2, "relations", fingerprint, [store.relations_path(kb_dir)],
                                 model_id=model_id, n_relations=len(existing))
    manifest.save(kb_dir, man2)
    log.audit(kb_dir, "ingest_relations", n_relations=len(out), model_id=model_id)
    return [], warnings


def ingest_topics(kb_dir: str, data: List[Dict[str, Any]], model_id: str,
                  reviewed: bool = False) -> Tuple[List[str], List[str]]:
    if isinstance(data, dict) and "topics" in data:
        data = data["topics"]
    if isinstance(data, dict):
        data = [data]
    man = manifest.load(kb_dir) or {}
    active = set(manifest.active_papers(man))
    known_claims = set()
    for pid in active:
        known_claims.update(c["claim_id"] for c in store.read_claims(kb_dir, pid))
    schema_path = os.path.join(cfgmod.SCHEMAS_DIR, "topic.schema.json")
    errors, warnings, written = [], [], []
    for raw in data:
        topic = dict(raw)
        topic["topic_id"] = topic.get("topic_id") or ids.topic_id(topic.get("name", "topic"))
        _stamp(topic, "topics", model_id, reviewed)
        missing_claims = _check_refs(topic, known_claims, active)
        if missing_claims:
            errors.extend(["topic %s: unknown reference %s" % (topic["topic_id"], m) for m in missing_claims[:10]])
        errs = jsonschema_lite.validate(topic, schema_path)
        if errs:
            errors.extend(["topic %s: %s" % (topic["topic_id"], e) for e in errs])
            continue
        if not errors:
            path = os.path.join(store.topics_dir(kb_dir), "%s.json" % topic["topic_id"])
            fsutil.ensure_dir(os.path.dirname(path))
            fsutil.atomic_write_json(path, topic)
            written.append(path)
    if errors:
        return errors, warnings
    man2 = manifest.load(kb_dir)
    fingerprint = manifest.corpus_fingerprint("topics", cfgmod.load(kb_dir), man2)
    manifest.record_corpus_stage(man2, "topics", fingerprint, written, model_id=model_id,
                                 n_topics=len(written))
    manifest.save(kb_dir, man2)
    log.audit(kb_dir, "ingest_topics", n_topics=len(written), model_id=model_id)
    return [], warnings


def ingest_overview(kb_dir: str, data: Dict[str, Any], model_id: str,
                    reviewed: bool = False) -> Tuple[List[str], List[str]]:
    man = manifest.load(kb_dir) or {}
    active = set(manifest.active_papers(man))
    known_claims = set()
    for pid in active:
        known_claims.update(c["claim_id"] for c in store.read_claims(kb_dir, pid))
    overview = dict(data)
    _stamp(overview, "overview", model_id, reviewed)
    known_topics = {t["topic_id"] for t in store.read_topics(kb_dir)}
    missing = _check_refs(overview, known_claims, active, known_topics)
    errors = ["overview: unknown reference %s" % m for m in missing[:20]]
    errs = jsonschema_lite.validate(overview, os.path.join(cfgmod.SCHEMAS_DIR, "corpus_overview.schema.json"))
    errors.extend(["overview: %s" % e for e in errs])
    if errors:
        return errors, []
    fsutil.atomic_write_json(store.overview_json_path(kb_dir), overview)
    man2 = manifest.load(kb_dir)
    fingerprint = manifest.corpus_fingerprint("overview", cfgmod.load(kb_dir), man2)
    manifest.record_corpus_stage(man2, "overview", fingerprint, [store.overview_json_path(kb_dir)],
                                 model_id=model_id)
    manifest.save(kb_dir, man2)
    log.audit(kb_dir, "ingest_overview", model_id=model_id)
    return [], []


def ingest_terminology(kb_dir: str, data: Dict[str, Any], model_id: str) -> Tuple[List[str], List[str]]:
    payload = dict(data if isinstance(data, dict) else {"terms": data})
    _stamp(payload, "topics", model_id, False)
    errs = jsonschema_lite.validate(payload, os.path.join(cfgmod.SCHEMAS_DIR, "terminology.schema.json"))
    if errs:
        return errs, []
    fsutil.atomic_write_json(store.terminology_path(kb_dir), payload)
    log.audit(kb_dir, "ingest_terminology", n_terms=len(payload.get("terms") or []), model_id=model_id)
    return [], []


# ------------------------------------------------------------------ helpers
def _resolve_locators(node: Any, kb_dir: str, paper_id: str, units, text, source, method,
                      warnings: List[str]) -> None:
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if key == "locators" and isinstance(value, list):
                node[key] = [locators.resolve(kb_dir, paper_id, loc, units, text, source, method)
                             for loc in value if isinstance(loc, dict)]
                for loc in node[key]:
                    for warning in loc.get("warnings") or []:
                        warnings.append(warning)
            else:
                _resolve_locators(value, kb_dir, paper_id, units, text, source, method, warnings)
    elif isinstance(node, list):
        for item in node:
            _resolve_locators(item, kb_dir, paper_id, units, text, source, method, warnings)


def _check_refs(node: Any, known_claims: set, known_papers: set, known_topics: set = None) -> List[str]:
    missing: List[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "claim_ids" and isinstance(value, list):
                missing.extend(["claim_id %s" % c for c in value if c not in known_claims])
            elif key in ("paper_ids", "papers") and isinstance(value, list):
                missing.extend(["paper_id %s" % p for p in value
                                if isinstance(p, str) and p not in known_papers])
            elif key == "paper_id" and isinstance(value, str) and value not in known_papers:
                missing.append("paper_id %s" % value)
            elif key == "topic_id" and known_topics is not None and isinstance(value, str) \
                    and value not in known_topics:
                missing.append("topic_id %s" % value)
            else:
                missing.extend(_check_refs(value, known_claims, known_papers, known_topics))
    elif isinstance(node, list):
        for item in node:
            missing.extend(_check_refs(item, known_claims, known_papers, known_topics))
    return missing


def _record(kb_dir: str, paper_id: str, stage: str, cfg, model_id: str, outputs, **extra) -> None:
    man = manifest.load(kb_dir)
    entry = man["papers"][paper_id]
    stages = entry.get("stages", {})
    upstream = {name: (stages.get(name) or {}).get("fingerprint") for name in manifest.PAPER_STAGES}
    if stage == "claims":
        upstream["profile"] = manifest.paper_fingerprint(
            "profile", cfg, entry["content_sha256"],
            (stages.get("parse") or {}).get("adapter", ""),
            (stages.get("parse") or {}).get("adapter_version", ""), upstream)
    fingerprint = manifest.paper_fingerprint(
        stage, cfg, entry["content_sha256"],
        (stages.get("parse") or {}).get("adapter", ""),
        (stages.get("parse") or {}).get("adapter_version", ""), upstream)
    manifest.record_paper_stage(man, paper_id, stage, fingerprint, outputs,
                                model_id=model_id, **extra)
    manifest.save(kb_dir, man)


def ingest_file(kb_dir: str, stage: str, path: str, paper_id: Optional[str], model_id: str,
                reviewed: bool = False) -> Tuple[List[str], List[str]]:
    data = _load_response(path)
    if stage == "profile":
        return ingest_profile(kb_dir, paper_id, data, model_id, reviewed)
    if stage == "claims":
        return ingest_claims(kb_dir, paper_id, data, model_id, reviewed)
    if stage == "relations":
        return ingest_relations(kb_dir, data, model_id, reviewed)
    if stage == "topics":
        return ingest_topics(kb_dir, data, model_id, reviewed)
    if stage == "overview":
        return ingest_overview(kb_dir, data, model_id, reviewed)
    if stage == "terminology":
        return ingest_terminology(kb_dir, data, model_id)
    raise SystemExit("unknown stage %r" % stage)
