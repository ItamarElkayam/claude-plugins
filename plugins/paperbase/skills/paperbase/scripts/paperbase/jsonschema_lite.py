"""A small, honest JSON-Schema validator (stdlib only).

`jsonschema` is not installed here, so this module implements the subset of
draft-07 that the paperbase schemas actually use.  It is deliberately strict:
an unknown keyword is reported as an error in the *schema* rather than silently
ignored, so a schema can never appear to validate more than it does.

Supported: $ref (local '#/definitions/x' and 'file.schema.json#/definitions/x'),
type, enum, const, properties, required, additionalProperties, patternProperties,
items (schema or tuple), minItems, maxItems, uniqueItems, minimum, maximum,
exclusiveMinimum, exclusiveMaximum, minLength, maxLength, pattern, oneOf, anyOf,
allOf, not, nullable-by-type-array, title/description/$schema/$id/examples/default
(annotations, ignored).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List

ANNOTATIONS = {
    "title", "description", "$schema", "$id", "examples", "default", "$comment",
    "definitions", "deprecated", "readOnly", "writeOnly", "format",
}
SUPPORTED = {
    "$ref", "type", "enum", "const", "properties", "required",
    "additionalProperties", "patternProperties", "items", "minItems", "maxItems",
    "uniqueItems", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minLength", "maxLength", "pattern", "oneOf", "anyOf", "allOf", "not",
} | ANNOTATIONS

_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "number": (int, float), "integer": int, "null": type(None),
}


class SchemaError(ValueError):
    pass


class Validator:
    def __init__(self, schema: Dict[str, Any], base_dir: str = None, store: Dict[str, Any] = None):
        self.schema = schema
        self.base_dir = base_dir
        self.store = store if store is not None else {}
        if base_dir and schema.get("$id"):
            self.store[schema["$id"]] = schema

    # ---------------- public API ----------------
    def validate(self, instance: Any, path: str = "$") -> List[str]:
        """Return a list of human-readable error strings (empty == valid)."""
        return self._check(instance, self.schema, path)

    # ---------------- internals ----------------
    def _load_ref(self, ref: str) -> Dict[str, Any]:
        file_part, _, frag = ref.partition("#")
        if file_part:
            if not self.base_dir:
                raise SchemaError("external $ref %r but no base_dir given" % ref)
            full = os.path.join(self.base_dir, file_part)
            if full not in self.store:
                with open(full, "r", encoding="utf-8") as fh:
                    self.store[full] = json.load(fh)
            doc = self.store[full]
        else:
            doc = self.schema
        node = doc
        for part in [p for p in frag.split("/") if p]:
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or part not in node:
                raise SchemaError("cannot resolve $ref %r" % ref)
            node = node[part]
        return node

    def _check(self, inst: Any, schema: Any, path: str) -> List[str]:
        if schema is True or schema == {}:
            return []
        if schema is False:
            return ["%s: schema forbids any value" % path]
        if not isinstance(schema, dict):
            raise SchemaError("%s: schema must be an object or boolean" % path)

        unknown = set(schema) - SUPPORTED
        if unknown:
            raise SchemaError("%s: unsupported schema keywords %s" % (path, sorted(unknown)))

        errs: List[str] = []
        if "$ref" in schema:
            target = self._load_ref(schema["$ref"])
            errs.extend(self._check(inst, target, path))
            # sibling keywords next to $ref are still evaluated (draft-07 ignores
            # them; we evaluate to stay strict rather than silently permissive)

        if "type" in schema:
            types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            ok = False
            for t in types:
                py = _TYPES.get(t)
                if py is None:
                    raise SchemaError("%s: unknown type %r" % (path, t))
                if t == "integer" and isinstance(inst, bool):
                    continue
                if t == "number" and isinstance(inst, bool):
                    continue
                if isinstance(inst, py):
                    ok = True
                    break
            if not ok:
                errs.append("%s: expected type %s, got %s" % (path, "|".join(types), _tname(inst)))
                return errs

        if "enum" in schema and inst not in schema["enum"]:
            errs.append("%s: %r not in enum %s" % (path, inst, schema["enum"]))
        if "const" in schema and inst != schema["const"]:
            errs.append("%s: expected const %r" % (path, schema["const"]))

        if isinstance(inst, dict):
            props = schema.get("properties", {})
            for key in schema.get("required", []):
                if key not in inst:
                    errs.append("%s: missing required property %r" % (path, key))
            patterns = schema.get("patternProperties", {})
            for key, value in inst.items():
                handled = False
                if key in props:
                    errs.extend(self._check(value, props[key], "%s.%s" % (path, key)))
                    handled = True
                for pat, sub in patterns.items():
                    if re.search(pat, key):
                        errs.extend(self._check(value, sub, "%s.%s" % (path, key)))
                        handled = True
                if not handled:
                    addl = schema.get("additionalProperties", True)
                    if addl is False:
                        errs.append("%s: unexpected property %r" % (path, key))
                    elif isinstance(addl, dict):
                        errs.extend(self._check(value, addl, "%s.%s" % (path, key)))

        if isinstance(inst, list):
            items = schema.get("items")
            if isinstance(items, list):
                for i, sub in enumerate(items):
                    if i < len(inst):
                        errs.extend(self._check(inst[i], sub, "%s[%d]" % (path, i)))
            elif items is not None:
                for i, val in enumerate(inst):
                    errs.extend(self._check(val, items, "%s[%d]" % (path, i)))
            if "minItems" in schema and len(inst) < schema["minItems"]:
                errs.append("%s: needs >= %d items, got %d" % (path, schema["minItems"], len(inst)))
            if "maxItems" in schema and len(inst) > schema["maxItems"]:
                errs.append("%s: needs <= %d items, got %d" % (path, schema["maxItems"], len(inst)))
            if schema.get("uniqueItems"):
                seen = [json.dumps(x, sort_keys=True) for x in inst]
                if len(set(seen)) != len(seen):
                    errs.append("%s: items must be unique" % path)

        if isinstance(inst, str):
            if "minLength" in schema and len(inst) < schema["minLength"]:
                errs.append("%s: string shorter than %d" % (path, schema["minLength"]))
            if "maxLength" in schema and len(inst) > schema["maxLength"]:
                errs.append("%s: string longer than %d" % (path, schema["maxLength"]))
            if "pattern" in schema and not re.search(schema["pattern"], inst):
                errs.append("%s: %r does not match /%s/" % (path, inst[:60], schema["pattern"]))

        if isinstance(inst, (int, float)) and not isinstance(inst, bool):
            for key, op, txt in (
                ("minimum", lambda a, b: a >= b, ">="),
                ("maximum", lambda a, b: a <= b, "<="),
                ("exclusiveMinimum", lambda a, b: a > b, ">"),
                ("exclusiveMaximum", lambda a, b: a < b, "<"),
            ):
                if key in schema and not op(inst, schema[key]):
                    errs.append("%s: %r must be %s %r" % (path, inst, txt, schema[key]))

        if "allOf" in schema:
            for i, sub in enumerate(schema["allOf"]):
                errs.extend(self._check(inst, sub, path))
        if "anyOf" in schema:
            if not any(not self._check(inst, sub, path) for sub in schema["anyOf"]):
                errs.append("%s: does not match anyOf" % path)
        if "oneOf" in schema:
            matches = sum(1 for sub in schema["oneOf"] if not self._check(inst, sub, path))
            if matches != 1:
                errs.append("%s: matched %d oneOf branches (expected 1)" % (path, matches))
        if "not" in schema and not self._check(inst, schema["not"], path):
            errs.append("%s: must not match subschema" % path)
        return errs


def _tname(value: Any) -> str:
    for name, py in _TYPES.items():
        if name in ("number", "integer") and isinstance(value, bool):
            continue
        if isinstance(value, py):
            return name
    return type(value).__name__


def load_schema(path: str) -> Validator:
    with open(path, "r", encoding="utf-8") as fh:
        schema = json.load(fh)
    return Validator(schema, base_dir=os.path.dirname(os.path.abspath(path)))


def validate(instance: Any, schema_path: str) -> List[str]:
    return load_schema(schema_path).validate(instance)
