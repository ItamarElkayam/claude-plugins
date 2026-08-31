"""Atomic writes and small filesystem helpers.

Every canonical artifact is written to a sibling temp file and os.replace()d into
place, so an interrupted run leaves either the previous version or the new one -
never a half-written file.  Multi-file stages additionally journal their intent in
<kb>/state/journal.json so `kb status` can report an interrupted stage.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from typing import Any, Dict, Iterable, List


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def atomic_write_text(path: str, text: str) -> None:
    ensure_dir(os.path.dirname(os.path.abspath(path)))
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".pb-tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def atomic_write_json(path: str, data: Any, indent: int = 2) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=indent, sort_keys=True) + "\n")


def atomic_write_jsonl(path: str, rows: Iterable[Dict[str, Any]]) -> None:
    body = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
    atomic_write_text(path, body)


def read_json(path: str, default: Any = None) -> Any:
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_jsonl(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as exc:
                raise ValueError("%s:%d: invalid JSONL (%s)" % (path, lineno, exc))
    return rows


def read_text(path: str, default: str = "") -> str:
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def rm_tree(path: str) -> None:
    if os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.exists(path):
        os.unlink(path)


def journal_begin(kb_dir: str, stage: str, targets: List[str]) -> None:
    atomic_write_json(
        os.path.join(kb_dir, "state", "journal.json"),
        {"stage": stage, "targets": targets, "status": "in_progress"},
    )


def journal_end(kb_dir: str) -> None:
    path = os.path.join(kb_dir, "state", "journal.json")
    if os.path.exists(path):
        os.unlink(path)


def journal_state(kb_dir: str) -> Dict[str, Any]:
    return read_json(os.path.join(kb_dir, "state", "journal.json"), {}) or {}
