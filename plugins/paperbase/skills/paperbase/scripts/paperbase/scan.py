"""Discover source documents in the paper directory (read-only).

The paper directory is never written to.  Every file is classified as parseable,
companion archive, or unsupported-with-a-reason - nothing is silently skipped.
"""
from __future__ import annotations

import fnmatch
import os
import zipfile
from typing import Any, Dict, List

from . import ids


def _ignored(rel: str, patterns: List[str]) -> bool:
    base = os.path.basename(rel)
    for pat in patterns:
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(base, pat):
            return True
    return False


def scan(paper_dir: str, cfg: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    ing = cfg["ingestion"]
    text_ext = tuple(e.lower() for e in ing["text_extensions"])
    arch_ext = tuple(e.lower() for e in ing["archive_extensions"])
    max_bytes = ing["max_file_mb"] * 1024 * 1024
    documents: List[Dict[str, Any]] = []
    archives: List[Dict[str, Any]] = []
    unsupported: List[Dict[str, Any]] = []

    if not os.path.isdir(paper_dir):
        raise SystemExit("paper directory not found: %s" % paper_dir)

    for root, dirs, files in os.walk(paper_dir, followlinks=ing["follow_symlinks"]):
        dirs[:] = [d for d in sorted(dirs) if not _ignored(d, ing["ignore_globs"]) and not d.startswith(".")]
        for name in sorted(files):
            abspath = os.path.join(root, name)
            rel = os.path.relpath(abspath, paper_dir)
            if _ignored(rel, ing["ignore_globs"]) or name.startswith("._"):
                continue
            try:
                size = os.path.getsize(abspath)
                mtime = int(os.path.getmtime(abspath))
            except OSError as exc:
                unsupported.append({"path": rel, "reason": "unreadable: %s" % exc, "size_bytes": None})
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in text_ext:
                if size > max_bytes:
                    unsupported.append({
                        "path": rel, "size_bytes": size,
                        "reason": "larger than ingestion.max_file_mb (%d MB); raise the limit in the KB config to include it"
                                  % ing["max_file_mb"]})
                    continue
                if size == 0:
                    unsupported.append({"path": rel, "size_bytes": 0, "reason": "empty file"})
                    continue
                documents.append({
                    "path": rel, "abspath": abspath, "size_bytes": size, "mtime": mtime,
                    "content_sha256": ids.sha256_file(abspath), "ext": ext,
                })
            elif ext in arch_ext:
                entry = {
                    "path": rel, "abspath": abspath, "size_bytes": size,
                    "content_sha256": ids.sha256_file(abspath), "ext": ext, "contents": [],
                    "note": "companion archive: catalogued but not parsed. Unpack it into the paper "
                            "directory if its contents should become knowledge.",
                }
                if ext == ".zip":
                    try:
                        with zipfile.ZipFile(abspath) as zf:
                            entry["contents"] = [
                                {"name": i.filename, "size": i.file_size}
                                for i in zf.infolist() if not i.is_dir()
                            ][:200]
                    except Exception as exc:
                        entry["note"] += " (listing failed: %s)" % exc
                archives.append(entry)
            else:
                unsupported.append({
                    "path": rel, "size_bytes": size,
                    "reason": "extension %r is not a supported document format "
                              "(add it to ingestion.text_extensions if it is plain text)" % ext,
                })
    return {"documents": documents, "archives": archives, "unsupported": unsupported}
