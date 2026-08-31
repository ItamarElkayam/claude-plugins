"""paperbase - build a durable, provenance-checked knowledge base from a paper directory.

Canonical knowledge lives in Markdown / JSONL / YAML under the KB directory; the
SQLite FTS5 index and any embedding cache are derived and rebuildable.
"""
from . import bootstrap as _bootstrap  # noqa: E402  (side effect: sys.path)

_bootstrap.activate()

SCHEMA_VERSION = "1.0.0"
PIPELINE_VERSION = "1.2.0"
__all__ = ["SCHEMA_VERSION", "PIPELINE_VERSION"]
