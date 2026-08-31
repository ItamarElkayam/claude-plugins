"""paperbase - build a durable, provenance-checked knowledge base from a paper directory.

Canonical knowledge lives in Markdown / JSONL / YAML under the KB directory; the
SQLite FTS5 index and any embedding cache are derived and rebuildable.
"""
SCHEMA_VERSION = "1.0.0"
PIPELINE_VERSION = "1.1.0"
__all__ = ["SCHEMA_VERSION", "PIPELINE_VERSION"]
