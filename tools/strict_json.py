#!/usr/bin/env python3
"""Strict JSON helpers for security- and provenance-sensitive artifacts.

Python's default decoder accepts duplicate object names and the non-standard
``NaN``/``Infinity`` constants.  Both are ambiguous at trust boundaries: two
consumers can validate different effective values from the same bytes.  These
helpers keep the familiar ``json.JSONDecodeError`` failure surface while
rejecting those extensions everywhere they are used.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object name is not allowed: {key!r}")
        value[key] = item
    return value


def loads(payload: str | bytes | bytearray) -> Any:
    """Decode standards-compliant JSON, rejecting ambiguous extensions."""

    if isinstance(payload, (bytes, bytearray)):
        try:
            text = bytes(payload).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise json.JSONDecodeError("JSON must be UTF-8", "", 0) from exc
    else:
        text = payload
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError:
        raise
    except (TypeError, ValueError) as exc:
        raise json.JSONDecodeError(str(exc), text, 0) from exc


def load_path(path: Path) -> Any:
    return loads(path.read_bytes())


def dumps(value: Any, **kwargs: Any) -> str:
    """Encode canonical JSON without silently emitting non-finite numbers."""

    if "allow_nan" in kwargs:
        raise TypeError("strict_json.dumps controls allow_nan")
    return json.dumps(value, allow_nan=False, **kwargs)
