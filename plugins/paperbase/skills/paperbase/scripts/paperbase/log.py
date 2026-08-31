"""Logging with quiet/normal/verbose modes and a machine-readable audit trail."""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict

QUIET, NORMAL, VERBOSE = 0, 1, 2
_level = NORMAL
_stream = sys.stderr


def set_level(quiet: bool = False, verbose: bool = False) -> None:
    global _level
    _level = QUIET if quiet else (VERBOSE if verbose else NORMAL)


def level() -> int:
    return _level


def info(msg: str, *args: Any) -> None:
    if _level >= NORMAL:
        _stream.write((msg % args if args else msg) + "\n")
        _stream.flush()


def debug(msg: str, *args: Any) -> None:
    if _level >= VERBOSE:
        _stream.write("  · " + (msg % args if args else msg) + "\n")
        _stream.flush()


def warn(msg: str, *args: Any) -> None:
    if _level >= NORMAL:
        _stream.write("WARN: " + (msg % args if args else msg) + "\n")
        _stream.flush()


def error(msg: str, *args: Any) -> None:
    _stream.write("ERROR: " + (msg % args if args else msg) + "\n")
    _stream.flush()


def audit(kb_dir: str, event: str, **fields: Any) -> None:
    """Append one event to <kb>/state/audit.jsonl (append-only, never rewritten)."""
    path = os.path.join(kb_dir, "state", "audit.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    record: Dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "event": event,
    }
    record.update(fields)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
