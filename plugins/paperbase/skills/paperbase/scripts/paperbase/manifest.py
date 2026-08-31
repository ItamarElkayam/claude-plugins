"""Processing state: content hashes, stage fingerprints, dependencies, tombstones.

Incremental correctness rests on one idea: every stage output records a
*fingerprint* of everything that could change it - source content hash, parser
identity and version, knowledge-schema version, analysis prompt version, model id
and the config hash.  A stage is stale iff its recorded fingerprint differs from
the freshly computed one.  Modification times are never used to decide staleness.

Dependencies (see references/knowledge-model.md#dependency-graph):

    parse   <- source content, parser adapter+version, parsing config
    units   <- parse, units config
    metadata<- parse, metadata config
    profile <- units, metadata, paper_analysis prompt, model, schema
    claims  <- profile, claim_extraction prompt, model, schema
    families<- metadata of ALL papers
    relations <- claims of ALL papers (scoped recompute for touched pairs)
    topics  <- claims + relations
    overview<- topics + relations + claims
    index   <- units + claims + relations + topics + profiles (cheap, derived)
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Dict, Iterable, List, Optional

from . import PIPELINE_VERSION, SCHEMA_VERSION, config as cfgmod, fsutil

PAPER_STAGES = ("parse", "units", "metadata", "profile", "claims")
CORPUS_STAGES = ("tuning", "families", "relations", "topics", "overview", "index")
MODEL_STAGES = ("profile", "claims", "relations", "topics", "overview")

_PROMPT_FOR_STAGE = {
    "profile": "paper_analysis.md",
    "claims": "claim_extraction.md",
    "relations": "relation_discovery.md",
    "topics": "topic_synthesis.md",
    "overview": "corpus_overview.md",
}


def manifest_path(kb_dir: str) -> str:
    return os.path.join(kb_dir, "state", "manifest.json")


def empty(paper_dir: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    return {
        "schema_version": SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "config_hash": cfgmod.config_hash(cfg),
        "paper_dir": os.path.abspath(paper_dir),
        "created": now,
        "updated": now,
        "papers": {},
        "corpus_stages": {},
        "archives": [],
        "unsupported": [],
        "tombstones": [],
    }


def load(kb_dir: str) -> Optional[Dict[str, Any]]:
    return fsutil.read_json(manifest_path(kb_dir), None)


def save(kb_dir: str, man: Dict[str, Any]) -> None:
    man["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    fsutil.atomic_write_json(manifest_path(kb_dir), man)


def _hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def paper_fingerprint(stage: str, cfg: Dict[str, Any], content_sha: str, adapter: str,
                      adapter_version: str, upstream: Dict[str, str]) -> str:
    payload: Dict[str, Any] = {
        "stage": stage,
        "pipeline": PIPELINE_VERSION,
        "schema": SCHEMA_VERSION,
        "content": content_sha,
    }
    if stage == "parse":
        payload.update({"adapter": adapter, "adapter_version": adapter_version,
                        "cfg": cfg["parsing"]})
    elif stage == "units":
        payload.update({"parse": upstream.get("parse"), "cfg": cfg["units"]})
    elif stage == "metadata":
        payload.update({"parse": upstream.get("parse"), "cfg": cfg["metadata"]})
    elif stage == "profile":
        payload.update({
            "units": upstream.get("units"), "metadata": upstream.get("metadata"),
            "prompt": cfgmod.prompt_version(_PROMPT_FOR_STAGE["profile"]),
            "model": cfg["analysis"]["model_id"],
        })
    elif stage == "claims":
        payload.update({
            "profile": upstream.get("profile"),
            "prompt": cfgmod.prompt_version(_PROMPT_FOR_STAGE["claims"]),
            "model": cfg["analysis"]["model_id"],
        })
    else:
        raise ValueError("unknown paper stage %r" % stage)
    return _hash(payload)


def corpus_fingerprint(stage: str, cfg: Dict[str, Any], man: Dict[str, Any]) -> str:
    def stage_fps(name: str) -> Dict[str, str]:
        return {
            pid: (entry.get("stages", {}).get(name) or {}).get("fingerprint")
            for pid, entry in sorted(man["papers"].items())
            if entry.get("status") == "active"
        }

    payload: Dict[str, Any] = {"stage": stage, "pipeline": PIPELINE_VERSION, "schema": SCHEMA_VERSION}
    if stage == "tuning":
        payload["inputs"] = stage_fps("units")
        payload["cfg"] = cfg["tuning"]
    elif stage == "families":
        payload["inputs"] = stage_fps("metadata")
        payload["cfg"] = cfg["metadata"]
    elif stage == "relations":
        payload["inputs"] = stage_fps("claims")
        payload["prompt"] = cfgmod.prompt_version(_PROMPT_FOR_STAGE["relations"])
        payload["model"] = cfg["analysis"]["model_id"]
    elif stage == "topics":
        payload["inputs"] = stage_fps("claims")
        payload["relations"] = (man["corpus_stages"].get("relations") or {}).get("fingerprint")
        payload["prompt"] = cfgmod.prompt_version(_PROMPT_FOR_STAGE["topics"])
        payload["model"] = cfg["analysis"]["model_id"]
    elif stage == "overview":
        payload["topics"] = (man["corpus_stages"].get("topics") or {}).get("fingerprint")
        payload["relations"] = (man["corpus_stages"].get("relations") or {}).get("fingerprint")
        payload["prompt"] = cfgmod.prompt_version(_PROMPT_FOR_STAGE["overview"])
        payload["model"] = cfg["analysis"]["model_id"]
    elif stage == "index":
        payload["units"] = stage_fps("units")
        payload["claims"] = stage_fps("claims")
        payload["profiles"] = stage_fps("profile")
        payload["relations"] = (man["corpus_stages"].get("relations") or {}).get("fingerprint")
        payload["topics"] = (man["corpus_stages"].get("topics") or {}).get("fingerprint")
    else:
        raise ValueError("unknown corpus stage %r" % stage)
    return _hash(payload)


def stage_entry(man: Dict[str, Any], paper_id: str, stage: str) -> Dict[str, Any]:
    return (man["papers"].get(paper_id, {}).get("stages", {}) or {}).get(stage) or {}


def record_paper_stage(man: Dict[str, Any], paper_id: str, stage: str, fingerprint: str,
                       outputs: Iterable[str] = (), **extra: Any) -> None:
    entry = man["papers"].setdefault(paper_id, {"paper_id": paper_id, "stages": {}})
    entry.setdefault("stages", {})[stage] = dict(
        {
            "fingerprint": fingerprint,
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "outputs": sorted(outputs),
        },
        **extra
    )


def record_corpus_stage(man: Dict[str, Any], stage: str, fingerprint: str,
                        outputs: Iterable[str] = (), **extra: Any) -> None:
    man["corpus_stages"][stage] = dict(
        {
            "fingerprint": fingerprint,
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "outputs": sorted(outputs),
        },
        **extra
    )


def is_stale(man: Dict[str, Any], paper_id: str, stage: str, expected: str) -> bool:
    entry = stage_entry(man, paper_id, stage)
    return entry.get("fingerprint") != expected


def corpus_stale(man: Dict[str, Any], stage: str, expected: str) -> bool:
    return (man["corpus_stages"].get(stage) or {}).get("fingerprint") != expected


def active_papers(man: Dict[str, Any]) -> List[str]:
    return sorted(pid for pid, e in man["papers"].items() if e.get("status") == "active")


def tombstone(man: Dict[str, Any], paper_id: str, reason: str) -> None:
    entry = man["papers"].get(paper_id)
    if not entry:
        return
    entry["status"] = "tombstoned"
    entry["tombstoned_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    entry["tombstone_reason"] = reason
    man["tombstones"].append({
        "paper_id": paper_id,
        "source_path": entry.get("source_path"),
        "content_sha256": entry.get("content_sha256"),
        "removed_at": entry["tombstoned_at"],
        "reason": reason,
    })


def model_work_pending(man: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, List[str]]:
    """Which model-dependent stages are missing or stale (drives `kb tasks`)."""
    pending: Dict[str, List[str]] = {stage: [] for stage in MODEL_STAGES}
    for pid in active_papers(man):
        entry = man["papers"][pid]
        stages = entry.get("stages", {})
        upstream = {name: (stages.get(name) or {}).get("fingerprint") for name in PAPER_STAGES}
        for stage in ("profile", "claims"):
            expected = paper_fingerprint(
                stage, cfg, entry["content_sha256"],
                (stages.get("parse") or {}).get("adapter", ""),
                (stages.get("parse") or {}).get("adapter_version", ""),
                upstream,
            )
            if is_stale(man, pid, stage, expected):
                pending[stage].append(pid)
            upstream[stage] = expected
    for stage in ("relations", "topics", "overview"):
        if corpus_stale(man, stage, corpus_fingerprint(stage, cfg, man)):
            pending[stage].append("*")
    return pending


def analysis_state(man: Dict[str, Any], cfg: Dict[str, Any], paper_id: str) -> Dict[str, str]:
    """Per-paper freshness of the model stages: missing | stale | current.

    Computed live from fingerprints so it is never a stale snapshot.
    """
    entry = man["papers"][paper_id]
    stages = entry.get("stages", {})
    upstream = {name: (stages.get(name) or {}).get("fingerprint") for name in PAPER_STAGES}
    state: Dict[str, str] = {}
    for stage in ("profile", "claims"):
        expected = paper_fingerprint(
            stage, cfg, entry["content_sha256"],
            (stages.get("parse") or {}).get("adapter", ""),
            (stages.get("parse") or {}).get("adapter_version", ""), upstream)
        recorded = (stages.get(stage) or {}).get("fingerprint")
        state[stage] = "missing" if recorded is None else ("current" if recorded == expected else "stale")
        upstream[stage] = expected
    return state
