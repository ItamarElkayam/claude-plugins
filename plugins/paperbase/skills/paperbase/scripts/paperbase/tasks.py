"""Work orders for the model-dependent stages.

Deterministic scripts never call a language model.  Instead `kb tasks` writes a
self-contained work order per pending stage: the prompt, the exact input material
(units with ids, pages, sections and offsets), the schema the answer must satisfy,
and the response path.  Claude Code (or any agent, or an optional LLM adapter)
performs the reasoning and `kb ingest` validates and installs the result.

This keeps cost visible: one work order == one model call you chose to make.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from . import (SCHEMA_VERSION, config as cfgmod, fsutil, indexdb, log, manifest,
               store)

STAGE_SPEC = {
    "profile": {"prompt": "paper_analysis.md", "schema": "paper_profile.schema.json", "scope": "paper"},
    "claims": {"prompt": "claim_extraction.md", "schema": "claim.schema.json", "scope": "paper"},
    "relations": {"prompt": "relation_discovery.md", "schema": "relation.schema.json", "scope": "corpus"},
    "topics": {"prompt": "topic_synthesis.md", "schema": "topic.schema.json", "scope": "corpus"},
    "overview": {"prompt": "corpus_overview.md", "schema": "corpus_overview.schema.json", "scope": "corpus"},
}


def _prompt_text(name: str) -> str:
    return cfgmod.prompt_text(name)


def _units_payload(kb_dir: str, pid: str, max_chars: int) -> Dict[str, Any]:
    units = store.read_units(kb_dir, pid)
    parse = store.read_parse(kb_dir, pid)
    total = 0
    rows = []
    truncated = False
    for unit in units:
        row = {
            "unit_id": unit["unit_id"],
            "kind": unit["kind"],
            "section_path": unit.get("section_path"),
            "page_index": unit.get("page_index_start"),
            "page_label": unit.get("page_label_start"),
            "char_start": unit["char_start"],
            "char_end": unit["char_end"],
            "text": unit["text"],
        }
        total += unit["n_chars"]
        if total > max_chars:
            truncated = True
            break
        rows.append(row)
    return {"units": rows, "truncated": truncated, "n_units_total": len(units),
            "parse_report": {k: parse.get(k) for k in
                             ("adapter", "text_quality", "needs_ocr", "n_pages", "sections", "warnings")}}


def emit(kb_dir: str, stage: str, paper_ids: Optional[List[str]] = None, limit: Optional[int] = None,
         max_chars: int = 140000, force: bool = False) -> List[Dict[str, Any]]:
    """Write work orders for `stage`; returns the order descriptors."""
    if stage not in STAGE_SPEC:
        raise SystemExit("unknown stage %r (choose from %s)" % (stage, ", ".join(sorted(STAGE_SPEC))))
    cfg = cfgmod.load(kb_dir)
    man = manifest.load(kb_dir)
    if man is None:
        raise SystemExit("no KB at %s - run `kb build` first" % kb_dir)
    spec = STAGE_SPEC[stage]
    pending = manifest.model_work_pending(man, cfg)
    out_dir = os.path.join(kb_dir, "tasks", stage)
    fsutil.ensure_dir(out_dir)
    orders: List[Dict[str, Any]] = []

    if spec["scope"] == "paper":
        targets = paper_ids or pending.get(stage) or []
        if force and paper_ids:
            targets = paper_ids
        if limit:
            targets = targets[:limit]
        for pid in targets:
            if pid not in man["papers"]:
                log.warn("unknown paper_id %s - skipped", pid)
                continue
            payload = _units_payload(kb_dir, pid, max_chars)
            record = man["papers"][pid]
            profile = store.read_profile(kb_dir, pid) if stage == "claims" else None
            order = {
                "stage": stage, "paper_id": pid, "kb_dir": kb_dir,
                "schema": os.path.join(cfgmod.SCHEMAS_DIR, spec["schema"]),
                "prompt": spec["prompt"],
                "prompt_version": cfgmod.prompt_version(spec["prompt"]),
                "schema_version": SCHEMA_VERSION,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
                "response_path": os.path.join(out_dir, "%s.response.json" % pid),
                "metadata": record.get("metadata"),
                "expects": ("one paper_profile object" if stage == "profile"
                            else "a JSON array of claim objects"),
            }
            body = _render_order(order, payload, profile)
            path = os.path.join(out_dir, "%s.order.md" % pid)
            fsutil.atomic_write_text(path, body)
            fsutil.atomic_write_json(os.path.join(out_dir, "%s.order.json" % pid),
                                     dict(order, input=payload))
            order["order_path"] = path
            orders.append(order)
    else:
        if not force and not pending.get(stage):
            return []
        conn = indexdb.connect(kb_dir, create=False)
        claims = [dict(r) for r in conn.execute(
            "SELECT c.*, p.title, p.year, p.document_type FROM claims c JOIN papers p "
            "ON p.paper_id=c.paper_id WHERE p.status='active'").fetchall()]
        papers = [dict(r) for r in conn.execute(
            "SELECT paper_id, title, authors, year, venue, document_type, publication_status, "
            "text_quality, abstract FROM papers WHERE status='active'").fetchall()]
        conn.close()
        existing = store.read_relations(kb_dir)
        payload = {
            "papers": papers,
            "claims": [{k: c[k] for k in ("claim_id", "paper_id", "statement", "claim_type",
                                          "evidence_status", "polarity", "subject", "intervention",
                                          "comparator", "outcome", "direction", "magnitude",
                                          "uncertainty", "conditions", "qualifiers", "evidence_type",
                                          "concepts", "methods", "datasets", "is_primary_evidence",
                                          "confidence")} for c in claims],
            "existing_relations": [{k: r.get(k) for k in ("relation_id", "source_paper_id",
                                                          "target_paper_id", "relation", "origin")}
                                   for r in existing],
            "topics": [{"topic_id": t["topic_id"], "name": t["name"]} for t in store.read_topics(kb_dir)],
        }
        order = {
            "stage": stage, "paper_id": None, "kb_dir": kb_dir,
            "schema": os.path.join(cfgmod.SCHEMAS_DIR, spec["schema"]),
            "prompt": spec["prompt"],
            "prompt_version": cfgmod.prompt_version(spec["prompt"]),
            "schema_version": SCHEMA_VERSION,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "response_path": os.path.join(out_dir, "%s.response.json" % stage),
            "expects": {"relations": "a JSON array of relation objects",
                        "topics": "a JSON array of topic objects",
                        "overview": "one corpus_overview object"}[stage],
        }
        body = _render_order(order, payload, None)
        path = os.path.join(out_dir, "%s.order.md" % stage)
        fsutil.atomic_write_text(path, body)
        fsutil.atomic_write_json(os.path.join(out_dir, "%s.order.json" % stage), dict(order, input=payload))
        order["order_path"] = path
        orders.append(order)
    log.audit(kb_dir, "tasks_emitted", stage=stage, count=len(orders),
              prompt_version=cfgmod.prompt_version(spec["prompt"]))
    return orders


def _render_order(order: Dict[str, Any], payload: Dict[str, Any], profile: Optional[Dict[str, Any]]) -> str:
    prompt = _prompt_text(order["prompt"])
    lines = [
        "# paperbase work order: %s%s" % (order["stage"], (" for %s" % order["paper_id"]) if order["paper_id"] else ""),
        "",
        "- prompt: `prompts/%s` (version `%s`)" % (order["prompt"], order["prompt_version"]),
        "- schema the answer MUST satisfy: `%s`" % order["schema"],
        "- knowledge-schema version: `%s`" % order["schema_version"],
        "- expected output: %s" % order["expects"],
        "- write the answer to: `%s`" % order["response_path"],
        "- then run: `kb ingest %s --stage %s%s`" % (
            order["kb_dir"], order["stage"],
            (" --paper %s" % order["paper_id"]) if order["paper_id"] else ""),
        "",
        "The full input is also machine-readable in the sibling `.order.json`.",
        "",
        "---",
        "",
        "## Instructions (prompt)",
        "",
        prompt,
        "",
        "---",
        "",
        "## Input",
        "",
    ]
    if order.get("metadata"):
        lines += ["### Bibliographic record", "", "```json",
                  json.dumps(order["metadata"], indent=2, ensure_ascii=False), "```", ""]
    if profile:
        lines += ["### Existing research profile for this paper", "", "```json",
                  json.dumps(profile.get("analysis"), indent=2, ensure_ascii=False)[:20000], "```", ""]
    if "units" in payload:
        report = payload.get("parse_report") or {}
        lines += ["### Parse report", "", "```json", json.dumps(report, indent=2, ensure_ascii=False), "```", ""]
        if payload.get("truncated"):
            lines.append("> ⚠ The unit list below was truncated to fit the order size. %d units exist in "
                         "total; read the rest from `docs/%s/units.jsonl` before concluding that "
                         "something is absent.\n" % (payload["n_units_total"], order["paper_id"]))
        lines += ["### Retrieval units (cite these unit_ids in every locator)", ""]
        for unit in payload["units"]:
            lines.append("#### %s · %s · page %s · §%s · chars %d-%d" % (
                unit["unit_id"], unit["kind"], unit.get("page_label"),
                " > ".join(unit.get("section_path") or []) or "(none)",
                unit["char_start"], unit["char_end"]))
            lines.append("")
            lines.append(unit["text"])
            lines.append("")
    else:
        lines += ["### Corpus material", "", "```json",
                  json.dumps(payload, indent=2, ensure_ascii=False)[:400000], "```", ""]
    return "\n".join(lines)
