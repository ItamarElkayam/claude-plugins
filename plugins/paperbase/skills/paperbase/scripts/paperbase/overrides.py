"""Human corrections: loaded from <kb>/overrides/*.yaml|json, applied last.

Design rules (references/validation.md#human-overrides):
  * overrides live in their own files and are never regenerated or rewritten;
  * they are applied deterministically AFTER automated processing, so a rebuild
    cannot silently lose them;
  * every overridden field is marked with source "human_override" in the
    artifact's provenance, so an agent can see a value was corrected by a person;
  * an override that references an unknown id is reported by `kb validate`
    (as unapplied_override) rather than dropped in silence.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Tuple

from . import config as cfgmod, jsonschema_lite, log, miniyaml

TEMPLATE = """# paperbase human overrides.
#
# Everything here wins over automated extraction and survives every rebuild.
# `kb validate` reports overrides whose ids no longer exist (nothing is dropped
# silently).  Supported keys: papers, claims, relations, duplicates, families,
# terminology - see schemas/overrides.schema.json.

# papers:
#   smith-2020-example-title-ab12cd:
#     metadata:
#       year: 2019            # printed year was wrong in the PDF header
#       venue: "Bioinformatics"
#     topics: ["error-correction"]
#     notes: "corrected from the journal landing page"
#
# claims:
#   smith-2020-example-title-ab12cd#c03-9f1a2b:
#     conditions: ["coverage >= 20x", "Illumina HiSeq only"]
#     notes: "the original extraction dropped the coverage condition"
#
# families:
#   - source_paper_id: doe-nd-supplementary-notes-771abc
#     target_paper_id: smith-2020-example-title-ab12cd
#     relation: supplement-of
#     reason: "same tool, supplementary notes"
"""


def overrides_dir(kb_dir: str) -> str:
    return os.path.join(kb_dir, "overrides")


def install_template(kb_dir: str) -> str:
    path = os.path.join(overrides_dir(kb_dir), "overrides.yaml")
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(TEMPLATE)
    return path


def load(kb_dir: str) -> Tuple[Dict[str, Any], List[str]]:
    """Merge every override file; later files win on conflict (sorted by name)."""
    merged: Dict[str, Any] = {"papers": {}, "claims": {}, "relations": [], "duplicates": [],
                              "families": [], "terminology": {}}
    problems: List[str] = []
    directory = overrides_dir(kb_dir)
    if not os.path.isdir(directory):
        return merged, problems
    schema = os.path.join(cfgmod.SCHEMAS_DIR, "overrides.schema.json")
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if not name.endswith((".yaml", ".yml", ".json")) or not os.path.isfile(path):
            continue
        try:
            if name.endswith(".json"):
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            else:
                data = miniyaml.load(path)
        except Exception as exc:
            problems.append("%s: could not be parsed (%s) - fix the file; it is NOT ignored" % (name, exc))
            continue
        if data is None:
            continue
        if not isinstance(data, dict):
            problems.append("%s: top level must be a mapping" % name)
            continue
        errs = jsonschema_lite.validate(data, schema)
        if errs:
            problems.append("%s: %s" % (name, "; ".join(errs[:5])))
            continue
        for pid, patch in (data.get("papers") or {}).items():
            merged["papers"].setdefault(pid, {}).update(patch)
        for cid, patch in (data.get("claims") or {}).items():
            merged["claims"].setdefault(cid, {}).update(patch)
        merged["relations"].extend(data.get("relations") or [])
        merged["duplicates"].extend(data.get("duplicates") or [])
        merged["families"].extend(data.get("families") or [])
        term = data.get("terminology") or {}
        for key, value in term.items():
            merged["terminology"].setdefault(key, {}).update(value or {})
        log.debug("loaded overrides from %s", name)
    return merged, problems


def apply_metadata(paper: Dict[str, Any], ov: Dict[str, Any]) -> Dict[str, Any]:
    """Patch one paper record's metadata, recording human_override provenance."""
    patch = (ov.get("papers") or {}).get(paper["paper_id"]) or {}
    if patch.get("exclude"):
        paper["status"] = "excluded_by_override"
        paper.setdefault("override_notes", []).append(
            patch.get("notes") or "excluded by human override")
    for field, value in (patch.get("metadata") or {}).items():
        paper["metadata"][field] = value
        paper["metadata_provenance"][field] = {"source": "human_override", "confidence": 1.0}
    if patch.get("topics"):
        paper["topics"] = patch["topics"]
        paper.setdefault("override_notes", []).append("topics set by human override")
    if patch.get("notes"):
        paper.setdefault("override_notes", []).append(patch["notes"])
    return paper


def apply_claims(claims: List[Dict[str, Any]], ov: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Patch/delete claims. Returns (claims, applied_ids)."""
    patches = ov.get("claims") or {}
    applied: List[str] = []
    out: List[Dict[str, Any]] = []
    for claim in claims:
        patch = patches.get(claim["claim_id"])
        if not patch:
            out.append(claim)
            continue
        applied.append(claim["claim_id"])
        if patch.get("delete"):
            continue
        for field, value in patch.items():
            if field in ("delete", "notes"):
                continue
            claim[field] = value
        claim.setdefault("generated_by", {})["reviewed_by_human"] = True
        claim["human_override"] = True
        if patch.get("notes"):
            claim["override_notes"] = patch["notes"]
        out.append(claim)
    return out, applied


def apply_relations(relations: List[Dict[str, Any]], ov: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Add human relations, honour deletions, and force origin=human_override."""
    from . import ids

    applied: List[str] = []
    by_id = {r["relation_id"]: r for r in relations}
    for spec in ov.get("relations") or []:
        rid = spec.get("relation_id") or ids.relation_id(
            spec["source_paper_id"], spec["relation"], spec.get("target_paper_id") or "",
            spec.get("dimension") or "")
        if spec.get("delete"):
            if rid in by_id:
                by_id.pop(rid)
                applied.append(rid)
            continue
        base = dict(by_id.get(rid) or {})
        base.update({k: v for k, v in spec.items() if k != "delete"})
        base["relation_id"] = rid
        base.setdefault("rationale", "human-authored relation")
        base.setdefault("confidence", 1.0)
        base["origin"] = "human_override"
        by_id[rid] = base
        applied.append(rid)
    return list(by_id.values()), applied


def apply_families(families: List[Dict[str, Any]], ov: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Replace heuristic family links with human-confirmed ones."""
    from . import ids

    applied: List[str] = []
    out = list(families)
    for spec in ov.get("families") or []:
        src = spec["source_paper_id"]
        out = [r for r in out if not (r["source_paper_id"] == src and r["relation"] in
                                      ("supplement-of", "preprint-of", "possible-duplicate-of", "version-of"))]
        if spec["relation"] == "unrelated":
            applied.append("%s:unrelated" % src)
            continue
        rid = ids.relation_id(src, spec["relation"], spec.get("target_paper_id") or "", "document_family")
        out.append({
            "relation_id": rid, "source_paper_id": src,
            "target_paper_id": spec.get("target_paper_id"), "relation": spec["relation"],
            "dimension": "document_family",
            "rationale": spec.get("reason") or "human-confirmed document family link",
            "confidence": 1.0, "origin": "human_override", "basis": "human",
            "evidence_source": [], "evidence_target": [],
        })
        applied.append(rid)
    for spec in ov.get("duplicates") or []:
        rid = ids.relation_id(spec["drop"], "possible-duplicate-of", spec["keep"], "document_family")
        out = [r for r in out if r["relation_id"] != rid]
        out.append({
            "relation_id": rid, "source_paper_id": spec["drop"], "target_paper_id": spec["keep"],
            "relation": "possible-duplicate-of", "dimension": "document_family",
            "rationale": "human-resolved duplicate: %s" % spec["reason"],
            "confidence": 1.0, "origin": "human_override",
            "basis": "human_duplicate_resolution",
            "same_work": bool(spec.get("same_work", True)),
            "evidence_source": [], "evidence_target": [],
        })
        applied.append(rid)
    return out, applied


def unapplied(ov: Dict[str, Any], known_papers: List[str], known_claims: List[str],
              applied_claims: List[str], applied_relations: List[str],
              applied_families: List[str]) -> List[str]:
    """Overrides whose target ids do not exist - surfaced, never silently ignored."""
    out: List[str] = []
    for pid in (ov.get("papers") or {}):
        if pid not in known_papers:
            out.append("papers.%s: unknown paper_id (typo, or the document left the corpus)" % pid)
    for cid in (ov.get("claims") or {}):
        if cid not in known_claims and cid not in applied_claims:
            out.append("claims.%s: unknown claim_id (it may have been regenerated with a new id)" % cid)
    for spec in ov.get("families") or []:
        if spec["source_paper_id"] not in known_papers:
            out.append("families[%s]: unknown source_paper_id" % spec["source_paper_id"])
        elif spec.get("target_paper_id") and spec["target_paper_id"] not in known_papers:
            out.append("families[%s]: unknown target_paper_id %s" % (spec["source_paper_id"], spec["target_paper_id"]))
    for spec in ov.get("relations") or []:
        for key in ("source_paper_id", "target_paper_id"):
            value = spec.get(key)
            if value and value not in known_papers:
                out.append("relations[%s]: unknown %s %s" % (spec.get("relation"), key, value))
    for spec in ov.get("duplicates") or []:
        for key in ("keep", "drop"):
            if spec[key] not in known_papers:
                out.append("duplicates: unknown paper_id %s" % spec[key])
    return out
