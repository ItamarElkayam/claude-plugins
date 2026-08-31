"""Minimal YAML subset reader/writer (stdlib only).

Why this exists: PyYAML is not installed in this environment and pip mirrors are
unreliable, but humans should still edit configuration and correction files in a
comfortable format.  Machine-generated artifacts use JSON/JSONL; YAML is used only
where a person types (config, overrides, SKILL.md frontmatter, eval questions).

SUPPORTED SUBSET (anything else raises YamlError with a line number):
  - nested block mappings   key: value        (indentation, spaces only)
  - block sequences         - item            (scalars, mappings, nested lists)
  - scalars                 plain / 'single' / "double"
  - null (null, ~, empty), booleans (true/false/yes/no/on/off), int, float
  - block scalars           key: |   key: |-   key: >   key: >-
  - flow collections        [a, b]  {a: 1}     (JSON-compatible only)
  - comments                # to end of line (outside quotes)
  - a leading '---' document marker and a trailing '...'
NOT supported: anchors/aliases, tags, multiple documents, complex keys,
merge keys, quoted multi-line scalars.
"""
from __future__ import annotations

import json
import re
from typing import Any, List, Tuple

__all__ = ["YamlError", "loads", "load", "dumps", "dump", "split_frontmatter"]


class YamlError(ValueError):
    """Raised on unsupported or malformed YAML, always with a line number."""


_BOOL_TRUE = {"true", "yes", "on"}
_BOOL_FALSE = {"false", "no", "off"}
_NULL = {"", "null", "~"}
_INT_RE = re.compile(r"^[-+]?\d+$")
_FLOAT_RE = re.compile(r"^[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?$")


def _strip_comment(line: str) -> str:
    out: List[str] = []
    quote = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            out.append(ch)
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out).rstrip()


def _scalar(raw: str, lineno: int) -> Any:
    s = raw.strip()
    if s.startswith("[") or s.startswith("{"):
        try:
            return json.loads(s)
        except Exception as exc:  # pragma: no cover - message path
            raise YamlError(
                "line %d: flow collection must be JSON-compatible (%s): %s"
                % (lineno, exc, s)
            )
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        body = s[1:-1]
        if s[0] == '"':
            body = body.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        else:
            body = body.replace("''", "'")
        return body
    low = s.lower()
    if low in _NULL:
        return None
    if low in _BOOL_TRUE:
        return True
    if low in _BOOL_FALSE:
        return False
    if _INT_RE.match(s):
        return int(s)
    if _FLOAT_RE.match(s):
        return float(s)
    return s


def _split_key(line: str, lineno: int) -> Tuple[str, str]:
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == ":" and (i + 1 == len(line) or line[i + 1] in " \t"):
            key = line[:i].strip()
            if len(key) >= 2 and key[0] == key[-1] and key[0] in "'\"":
                key = key[1:-1]
            if not key:
                raise YamlError("line %d: empty mapping key" % lineno)
            return key, line[i + 1 :].strip()
    raise YamlError(
        "line %d: expected 'key: value' or '- item', got %r" % (lineno, line.strip())
    )


class _Reader:
    def __init__(self, text: str) -> None:
        raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self.lines: List[Tuple[int, str, str]] = []  # (lineno, indent-stripped, raw)
        for n, line in enumerate(raw, start=1):
            if "\t" in line[: len(line) - len(line.lstrip())]:
                raise YamlError("line %d: tab indentation is not supported" % n)
            self.lines.append((n, line, line))
        self.pos = 0

    def _peek(self):
        while self.pos < len(self.lines):
            n, line, _ = self.lines[self.pos]
            stripped = _strip_comment(line)
            if not stripped.strip():
                self.pos += 1
                continue
            if stripped.strip() in ("---", "..."):
                self.pos += 1
                continue
            indent = len(stripped) - len(stripped.lstrip(" "))
            return n, indent, stripped.strip(), stripped
        return None

    def parse(self) -> Any:
        head = self._peek()
        if head is None:
            return None
        value = self._parse_block(head[1])
        rest = self._peek()
        if rest is not None:
            raise YamlError("line %d: unexpected content at indent %d" % (rest[0], rest[1]))
        return value

    def _parse_block(self, indent: int) -> Any:
        head = self._peek()
        if head is None:
            return None
        if head[2].startswith("- "):
            return self._parse_seq(indent)
        if head[2] == "-":
            return self._parse_seq(indent)
        return self._parse_map(indent)

    def _parse_seq(self, indent: int) -> List[Any]:
        items: List[Any] = []
        while True:
            head = self._peek()
            if head is None or head[1] < indent:
                return items
            n, ind, content, _ = head
            if ind > indent:
                raise YamlError("line %d: unexpected indentation in list" % n)
            if not (content == "-" or content.startswith("- ")):
                return items
            self.pos += 1
            rest = content[1:].strip()
            if not rest:
                nxt = self._peek()
                if nxt is not None and nxt[1] > indent:
                    items.append(self._parse_block(nxt[1]))
                else:
                    items.append(None)
                continue
            # inline content after the dash
            if re.match(r"^[^:]+:(\s|$)", rest) or _is_mapping_start(rest):
                items.append(self._parse_inline_map(rest, n, indent + 2))
            else:
                items.append(_scalar(rest, n))
        return items

    def _parse_inline_map(self, first: str, lineno: int, child_indent: int) -> Any:
        key, val = _split_key(first, lineno)
        result: Any = {}
        result[key] = self._value_for(key, val, lineno, child_indent)
        nxt = self._peek()
        if nxt is not None and nxt[1] >= child_indent:
            more = self._parse_map(nxt[1])
            if isinstance(more, dict):
                for k, v in more.items():
                    result[k] = v
        return result

    def _parse_map(self, indent: int) -> Any:
        out = {}
        while True:
            head = self._peek()
            if head is None or head[1] < indent:
                return out
            n, ind, content, _ = head
            if ind > indent:
                raise YamlError("line %d: unexpected indentation in mapping" % n)
            if content.startswith("- "):
                return out
            self.pos += 1
            key, val = _split_key(content, n)
            out[key] = self._value_for(key, val, n, indent)
        return out

    def _value_for(self, key: str, val: str, lineno: int, indent: int) -> Any:
        if val in ("|", "|-", ">", ">-", "|+", ">+"):
            return self._parse_block_scalar(val, indent)
        if val == "":
            nxt = self._peek()
            if nxt is not None and nxt[1] > indent:
                return self._parse_block(nxt[1])
            return None
        return _scalar(val, lineno)

    def _parse_block_scalar(self, marker: str, indent: int) -> str:
        body: List[str] = []
        block_indent = None
        while self.pos < len(self.lines):
            n, line, _ = self.lines[self.pos]
            if not line.strip():
                body.append("")
                self.pos += 1
                continue
            cur = len(line) - len(line.lstrip(" "))
            if cur <= indent:
                break
            if block_indent is None:
                block_indent = cur
            body.append(line[block_indent:] if len(line) > block_indent else "")
            self.pos += 1
        while body and body[-1] == "":
            body.pop()
        if marker.startswith(">"):
            text = " ".join(x.strip() for x in body if x.strip())
        else:
            text = "\n".join(body)
        if marker.endswith("-"):
            return text
        return text + "\n" if text else text


def _is_mapping_start(text: str) -> bool:
    try:
        _split_key(text, 0)
        return True
    except YamlError:
        return False


def loads(text: str) -> Any:
    """Parse a YAML subset document into Python objects."""
    return _Reader(text).parse()


def load(path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        try:
            return loads(fh.read())
        except YamlError as exc:
            raise YamlError("%s: %s" % (path, exc))


_PLAIN_SAFE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9 _./+()@:-]*$")


def _emit_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value)
    if text == "":
        return '""'
    reserved = text.lower() in (_BOOL_TRUE | _BOOL_FALSE | {"null", "~"})
    if "\n" in text:
        return None  # caller uses a block scalar
    if reserved or not _PLAIN_SAFE.match(text) or text.endswith(":"):
        return json.dumps(text, ensure_ascii=False)
    return text


def dumps(data: Any, indent: int = 0) -> str:
    """Serialise Python objects to the supported YAML subset."""
    pad = " " * indent
    lines: List[str] = []
    if isinstance(data, dict):
        if not data:
            return pad + "{}\n"
        for key, value in data.items():
            k = _emit_scalar(key) or json.dumps(str(key))
            if isinstance(value, dict) and value:
                lines.append("%s%s:" % (pad, k))
                lines.append(dumps(value, indent + 2).rstrip("\n"))
            elif isinstance(value, (list, tuple)) and value:
                lines.append("%s%s:" % (pad, k))
                lines.append(dumps(list(value), indent + 2).rstrip("\n"))
            elif isinstance(value, str) and "\n" in value:
                # '|' keeps exactly one trailing newline, '|-' strips it: pick the
                # marker that round-trips the value byte-for-byte.
                marker = "|" if value.endswith("\n") and not value.endswith("\n\n") else "|-"
                lines.append("%s%s: %s" % (pad, k, marker))
                for ln in value.rstrip("\n").split("\n"):
                    lines.append("%s  %s" % (pad, ln))
            else:
                if isinstance(value, (list, tuple)):
                    lines.append("%s%s: []" % (pad, k))
                elif isinstance(value, dict):
                    lines.append("%s%s: {}" % (pad, k))
                else:
                    lines.append("%s%s: %s" % (pad, k, _emit_scalar(value)))
        return "\n".join(lines) + "\n"
    if isinstance(data, (list, tuple)):
        if not data:
            return pad + "[]\n"
        for item in data:
            if isinstance(item, dict) and item:
                block = dumps(item, indent + 2).rstrip("\n").split("\n")
                first = block[0].lstrip()
                lines.append("%s- %s" % (pad, first))
                lines.extend(block[1:])
            elif isinstance(item, (list, tuple)) and item:
                block = dumps(list(item), indent + 2).rstrip("\n")
                lines.append("%s-" % pad)
                lines.append(block)
            else:
                lines.append("%s- %s" % (pad, _emit_scalar(item)))
        return "\n".join(lines) + "\n"
    return pad + (_emit_scalar(data) or json.dumps(str(data))) + "\n"


def dump(data: Any, path) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(dumps(data))


def split_frontmatter(text: str):
    """Return (frontmatter_dict, body) for a '---' delimited Markdown file."""
    if not text.startswith("---"):
        return None, text
    lines = text.split("\n")
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            end = i
            break
    if end is None:
        return None, text
    fm = loads("\n".join(lines[1:end]))
    return fm, "\n".join(lines[end + 1 :])
