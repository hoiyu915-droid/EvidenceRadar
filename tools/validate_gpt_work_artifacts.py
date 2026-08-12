#!/usr/bin/env python3
"""Validate EvidenceRadar artifacts from either execution lane without dependencies.

The repository intentionally keeps this validator small and dependency-free.
It implements the JSON Schema vocabulary used by the three checked-in
EvidenceRadar schemas (types, required/properties, references, composition,
arrays, scalar constraints and the date/URI formats used by the artifacts).
It is a structural gate, not a claim truth checker: source URLs and locators
still require human review of the referenced material.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

# This validator is shipped inside the read-only Work Pack.
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.strict_json import load_path as strict_json_load_path

JsonValue = Any


def _json_type_matches(value: JsonValue, expected: str) -> bool:
    """Return whether *value* has the JSON Schema type *expected*."""

    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        # bool is a subclass of int in Python, but not a JSON integer.
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and (not isinstance(value, float) or math.isfinite(value))
        )
    return True


def _matches_type(value: JsonValue, expected: str | list[str]) -> bool:
    if isinstance(expected, list):
        return any(_json_type_matches(value, item) for item in expected)
    return _json_type_matches(value, expected)


def _resolve_local_ref(root: dict[str, Any], reference: str) -> dict[str, Any] | None:
    """Resolve the local JSON Pointer references used by the schemas."""

    if reference == "#":
        return root
    if not reference.startswith("#/"):
        return None
    current: Any = root
    for token in reference[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or token not in current:
            return None
        current = current[token]
    return current if isinstance(current, dict) else None


def _format_error(path: str, message: str) -> str:
    return f"{path}: {message}"


def _valid_format(value: str, format_name: str) -> bool:
    if format_name == "date-time":
        try:
            parsed = _datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return parsed.tzinfo is not None
    if format_name == "date":
        try:
            _datetime.date.fromisoformat(value)
        except ValueError:
            return False
        return True
    if format_name == "uri":
        parsed = urlparse(value)
        return bool(parsed.scheme and (parsed.netloc or parsed.scheme == "urn"))
    # Unknown formats are annotations in JSON Schema.  They do not make an
    # otherwise valid fixture fail this local structural validator.
    return True


def _unique(values: list[Any]) -> bool:
    try:
        encoded = [
            json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for item in values
        ]
    except (TypeError, ValueError):
        return False
    return len(encoded) == len(set(encoded))


def _validate(
    value: JsonValue,
    schema: dict[str, Any],
    root: dict[str, Any],
    path: str,
    errors: list[str],
) -> None:
    """Append structural validation errors for one value."""

    reference = schema.get("$ref")
    if reference is not None:
        target = _resolve_local_ref(root, str(reference))
        if target is None:
            errors.append(_format_error(path, f"unresolvable $ref {reference!r}"))
        else:
            _validate(value, target, root, path, errors)
        return

    if "allOf" in schema:
        for index, branch in enumerate(schema["allOf"]):
            _validate(value, branch, root, path, errors)

    if "anyOf" in schema:
        branch_errors: list[list[str]] = []
        for branch in schema["anyOf"]:
            candidate: list[str] = []
            _validate(value, branch, root, path, candidate)
            branch_errors.append(candidate)
        if not any(not candidate for candidate in branch_errors):
            errors.append(_format_error(path, "does not match anyOf alternatives"))

    if "oneOf" in schema:
        matches = 0
        branch_errors: list[list[str]] = []
        for branch in schema["oneOf"]:
            candidate = []
            _validate(value, branch, root, path, candidate)
            branch_errors.append(candidate)
            if not candidate:
                matches += 1
        if matches != 1:
            errors.append(_format_error(path, f"matches {matches} oneOf alternatives; expected exactly one"))
            # When no branch matches, retain the nested diagnostics as well;
            # this makes a missing measurement field visible to the caller
            # instead of returning only the composition summary.
            if matches == 0:
                for candidate in branch_errors:
                    errors.extend(candidate)

    if "not" in schema:
        candidate = []
        _validate(value, schema["not"], root, path, candidate)
        if not candidate:
            errors.append(_format_error(path, "matches a forbidden schema"))

    expected = schema.get("type")
    if expected is not None and not _matches_type(value, expected):
        errors.append(_format_error(path, f"expected type {expected!r}, got {type(value).__name__}"))
        # The remaining keywords are type-specific and would only produce
        # noisy follow-up errors when the value has the wrong type.
        return

    if "const" in schema and value != schema["const"]:
        errors.append(_format_error(path, f"must equal {schema['const']!r}"))

    if "enum" in schema and value not in schema["enum"]:
        errors.append(_format_error(path, f"must be one of {schema['enum']!r}"))

    if isinstance(value, dict):
        for required in schema.get("required", []):
            if required not in value:
                errors.append(_format_error(path, f"missing required property {required!r}"))

        properties = schema.get("properties", {})
        for name, property_schema in properties.items():
            if name in value:
                _validate(value[name], property_schema, root, f"{path}.{name}", errors)

        additional = schema.get("additionalProperties", True)
        if additional is False:
            known = set(properties)
            for name in value:
                if name not in known:
                    errors.append(_format_error(path, f"unexpected property {name!r}"))
        elif isinstance(additional, dict):
            known = set(properties)
            for name, item in value.items():
                if name not in known:
                    _validate(item, additional, root, f"{path}.{name}", errors)

        if "minProperties" in schema and len(value) < schema["minProperties"]:
            errors.append(_format_error(path, f"requires at least {schema['minProperties']} properties"))
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            errors.append(_format_error(path, f"allows at most {schema['maxProperties']} properties"))

    if isinstance(value, list):
        if "items" in schema:
            for index, item in enumerate(value):
                _validate(item, schema["items"], root, f"{path}[{index}]", errors)
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(_format_error(path, f"requires at least {schema['minItems']} items"))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(_format_error(path, f"allows at most {schema['maxItems']} items"))
        if schema.get("uniqueItems") and not _unique(value):
            errors.append(_format_error(path, "items must be unique"))

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(_format_error(path, f"requires at least {schema['minLength']} characters"))
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(_format_error(path, f"allows at most {schema['maxLength']} characters"))
        pattern = schema.get("pattern")
        if pattern is not None:
            try:
                matched = re.search(str(pattern), value) is not None
            except re.error as exc:
                errors.append(_format_error(path, f"invalid schema pattern: {exc}"))
                matched = True
            if not matched:
                errors.append(_format_error(path, f"does not match pattern {pattern!r}"))
        format_name = schema.get("format")
        if format_name and not _valid_format(value, str(format_name)):
            errors.append(_format_error(path, f"does not match format {format_name!r}"))

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(_format_error(path, f"must be >= {schema['minimum']}"))
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(_format_error(path, f"must be <= {schema['maximum']}"))
        if "exclusiveMinimum" in schema:
            bound = schema["exclusiveMinimum"]
            if isinstance(bound, (int, float)) and value <= bound:
                errors.append(_format_error(path, f"must be > {bound}"))
        if "exclusiveMaximum" in schema:
            bound = schema["exclusiveMaximum"]
            if isinstance(bound, (int, float)) and value >= bound:
                errors.append(_format_error(path, f"must be < {bound}"))


def validate_document(document: JsonValue, schema: dict[str, Any]) -> list[str]:
    """Return all structural validation errors for a decoded JSON document."""

    if not isinstance(schema, dict):
        return ["$: schema must be a JSON object"]
    errors: list[str] = []
    _validate(document, schema, schema, "$", errors)
    return errors


def load_json(path: Path) -> Any:
    return strict_json_load_path(path)


def schema_for_artifact(artifact_path: Path, schema_dir: Path) -> Path:
    suffix = artifact_path.stem.removeprefix("EvidenceRadar_").replace("_", "-").lower()
    return schema_dir / f"evidence-radar-{suffix}.schema.json"


def validate_files(
    artifact_paths: Iterable[Path],
    *,
    schema_dir: Path,
) -> list[str]:
    """Validate files and return human-readable errors (empty means valid)."""

    errors: list[str] = []
    for artifact_path in artifact_paths:
        schema_path = schema_for_artifact(artifact_path, schema_dir)
        if not schema_path.exists():
            errors.append(f"{artifact_path}: schema not found at {schema_path}")
            continue
        try:
            document = load_json(artifact_path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{artifact_path}: cannot load JSON: {exc}")
            continue
        try:
            schema = load_json(schema_path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{schema_path}: cannot load JSON: {exc}")
            continue
        for message in validate_document(document, schema):
            errors.append(f"{artifact_path}: {message}")
    return errors


def _default_artifacts(root: Path) -> list[Path]:
    return sorted((root / "examples").glob("EvidenceRadar_*.json"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "artifacts",
        nargs="*",
        type=Path,
        help="artifact JSON files; defaults to all checked-in examples",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="EvidenceRadar-gpt-work root (used for default examples/schemas)",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    artifacts = [path.resolve() for path in args.artifacts] if args.artifacts else _default_artifacts(root)
    if not artifacts:
        print(f"No artifact files found under {root / 'examples'}", file=sys.stderr)
        return 2
    errors = validate_files(artifacts, schema_dir=root / "schemas")
    if errors:
        for error in errors:
            print(f"ERROR {error}", file=sys.stderr)
        return 1
    for artifact in artifacts:
        print(f"OK {artifact}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by CLI tests/manual use
    raise SystemExit(main())
