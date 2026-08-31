"""Configuration loading, merging and hashing."""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict

from . import miniyaml

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULTS_PATH = os.path.join(SKILL_ROOT, "config", "defaults.yaml")
SCHEMAS_DIR = os.path.join(SKILL_ROOT, "schemas")
PROMPTS_DIR = os.path.join(SKILL_ROOT, "prompts")


def load_defaults() -> Dict[str, Any]:
    return miniyaml.load(DEFAULTS_PATH)


def _deep_merge(base: Dict[str, Any], over: Dict[str, Any], path: str = "") -> Dict[str, Any]:
    out = dict(base)
    for key, value in (over or {}).items():
        where = "%s.%s" % (path, key) if path else key
        if key not in base:
            raise ValueError(
                "unknown config key %r (see %s for the documented key set)" % (where, DEFAULTS_PATH)
            )
        if isinstance(base[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(base[key], value, where)
        else:
            out[key] = value
    return out


def kb_config_path(kb_dir: str) -> str:
    return os.path.join(kb_dir, "config", "config.yaml")


def load(kb_dir: str = None) -> Dict[str, Any]:
    """Defaults, overlaid by <kb>/config/config.yaml when present."""
    cfg = load_defaults()
    if kb_dir:
        path = kb_config_path(kb_dir)
        if os.path.exists(path):
            cfg = _deep_merge(cfg, miniyaml.load(path) or {})
    return cfg


def install(kb_dir: str) -> str:
    """Write the editable per-KB config copy if it does not exist yet."""
    path = kb_config_path(kb_dir)
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(DEFAULTS_PATH, "r", encoding="utf-8") as src:
            body = src.read()
        with open(path, "w", encoding="utf-8") as dst:
            dst.write(body)
    return path


def config_hash(cfg: Dict[str, Any]) -> str:
    blob = json.dumps(cfg, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


INCLUDE_RE = re.compile(r"\{\{include:\s*([A-Za-z0-9_.\-]+)\s*\}\}")


def prompt_text(name: str) -> str:
    """Prompt file with {{include: other.md}} directives expanded (one level)."""
    path = os.path.join(PROMPTS_DIR, name)
    if not os.path.exists(path):
        raise SystemExit("prompt file missing: %s" % path)
    with open(path, "r", encoding="utf-8") as fh:
        body = fh.read()

    def _sub(match):
        inc = os.path.join(PROMPTS_DIR, match.group(1))
        if not os.path.exists(inc):
            raise SystemExit("prompt %s includes missing file %s" % (name, match.group(1)))
        with open(inc, "r", encoding="utf-8") as fh2:
            return fh2.read()

    return INCLUDE_RE.sub(_sub, body)


def prompt_version(name: str) -> str:
    """Content hash of a prompt (including its includes): an edit invalidates analyses."""
    path = os.path.join(PROMPTS_DIR, name)
    if not os.path.exists(path):
        return "missing"
    try:
        body = prompt_text(name)
    except SystemExit:
        return "broken"
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
