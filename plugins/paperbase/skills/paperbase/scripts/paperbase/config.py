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


# Paths whose keys are DATA, not schema: users and the tuner invent the keys, so the
# unknown-key guard must not recurse into them.
FREEFORM_PATHS = frozenset(["terminology.synonyms", "terminology.acronyms"])


def _deep_merge(base: Dict[str, Any], over: Dict[str, Any], path: str = "") -> Dict[str, Any]:
    out = dict(base)
    for key, value in (over or {}).items():
        where = "%s.%s" % (path, key) if path else key
        if where in FREEFORM_PATHS:
            if not isinstance(value, dict):
                raise ValueError("config key %r must be a mapping, got %s"
                                 % (where, type(value).__name__))
            out[key] = dict(value)
            continue
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
    """The human-edited config: authoritative, never written by paperbase."""
    return os.path.join(kb_dir, "config", "config.yaml")


def derived_path(kb_dir: str) -> str:
    """Machine-mined config (see tune.py): layered UNDER the human config."""
    return os.path.join(kb_dir, "config", "derived.yaml")


def load(kb_dir: str = None) -> Dict[str, Any]:
    """Effective config: defaults <- derived.yaml (mined) <- config.yaml (human).

    The human file is applied last, so a hand-set value can never be overwritten by
    automatic tuning, and setting a key to [] or {} there suppresses the mined value.
    """
    cfg = load_defaults()
    if not kb_dir:
        return cfg
    for path, label in ((derived_path(kb_dir), "derived"), (kb_config_path(kb_dir), "config")):
        if not os.path.exists(path):
            continue
        try:
            overlay = miniyaml.load(path) or {}
        except Exception as exc:
            raise SystemExit("%s is not readable (%s). Fix or delete it; paperbase will not "
                             "guess what you meant." % (path, exc))
        try:
            cfg = _deep_merge(cfg, overlay)
        except ValueError as exc:
            raise SystemExit("%s: %s" % (path, exc))
    return cfg


TEMPLATE_HEADER = """# paperbase configuration for THIS knowledge base.
#
# Every setting below is commented out and shows the built-in default. Uncomment only
# what you want to change - including the parent key, e.g.
#
#   units:
#     max_chars: 2000
#
# Why commented: values you set here override BOTH the built-in defaults and the
# vocabulary mined automatically into config/derived.yaml. A key left commented lets the
# mined value apply; setting it to [] or {} suppresses the mined value entirely.
#
# Unknown keys are rejected by name, so a typo fails loudly instead of doing nothing.
# ---------------------------------------------------------------------------------
"""


def install(kb_dir: str) -> str:
    """Write the per-KB config as a fully commented template, once.

    A full uncommented copy would pin every key to its default and so silently
    override the automatically mined vocabulary in derived.yaml.
    """
    path = kb_config_path(kb_dir)
    if os.path.exists(path):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(DEFAULTS_PATH, "r", encoding="utf-8") as src:
        lines = src.read().split("\n")
    body = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            body.append(line)
        else:
            body.append("# " + line)
    with open(path, "w", encoding="utf-8") as dst:
        dst.write(TEMPLATE_HEADER + "\n".join(body))
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
