#!/usr/bin/env python3
"""Shared outbound-network boundary checks and bounded response readers."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

DEFAULT_RESPONSE_LIMIT = 8 * 1024 * 1024


class UnsafeUrlError(ValueError):
    """Raised before an unsafe or unresolvable HTTP target is requested."""


class ResponseTooLargeError(ValueError):
    """Raised when a response exceeds its declared or observed byte budget."""


Resolver = Callable[[str, int | None], Iterable[str]]


def _system_resolver(hostname: str, port: int | None) -> list[str]:
    try:
        records = socket.getaddrinfo(
            hostname,
            port or 443,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"publisher hostname cannot be resolved: {hostname}") from exc
    return sorted({str(record[4][0]).split("%", 1)[0] for record in records})


def _public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_global


def validate_public_http_url(
    raw_url: str,
    *,
    resolver: Resolver = _system_resolver,
) -> str:
    """Return a normalized public HTTP(S) URL or fail before network access.

    Every redirect hop must be validated separately.  Requiring every DNS
    answer to be globally routable prevents a mixed public/private response
    from becoming a DNS-rebinding bypass.
    """

    try:
        parsed = urlsplit(str(raw_url).strip())
        port = parsed.port
    except ValueError as exc:
        raise UnsafeUrlError("publisher URL is malformed") from exc
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise UnsafeUrlError("publisher URL must use HTTP or HTTPS")
    if not parsed.hostname:
        raise UnsafeUrlError("publisher URL is missing a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeUrlError("publisher URL must not contain user information")
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").casefold()
    except UnicodeError as exc:
        raise UnsafeUrlError("publisher hostname is invalid") from exc
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        addresses = list(resolver(hostname, port))
    else:
        addresses = [hostname]
    if not addresses or any(not _public_address(address) for address in addresses):
        raise UnsafeUrlError(
            f"publisher hostname is not exclusively public: {hostname}"
        )
    host = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        host = f"{host}:{port}"
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            host,
            parsed.path or "/",
            parsed.query,
            "",
        )
    )


def _declared_length(response: Any) -> int | None:
    raw = str(getattr(response, "headers", {}).get("Content-Length") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def bounded_response_bytes(
    response: Any,
    *,
    limit: int = DEFAULT_RESPONSE_LIMIT,
) -> bytes | None:
    """Read a streamed response once, enforcing both declared and actual size.

    Lightweight test doubles that expose neither bytes nor an iterator return
    ``None``; production ``requests.Response`` objects always take the bounded
    iterator path.
    """

    declared = _declared_length(response)
    if declared is not None and declared > limit:
        raise ResponseTooLargeError(
            f"response Content-Length {declared} exceeds {limit} bytes"
        )
    iterator = getattr(response, "iter_content", None)
    if callable(iterator):
        try:
            stream = iter(iterator(chunk_size=64 * 1024))
        except TypeError:
            # Plain ``Mock`` response fixtures synthesize a callable attribute
            # that is not an iterator. Real requests responses never do this.
            stream = None
        if stream is not None:
            chunks: list[bytes] = []
            observed = 0
            for chunk in stream:
                if not chunk:
                    continue
                data = bytes(chunk)
                observed += len(data)
                if observed > limit:
                    raise ResponseTooLargeError(
                        f"response body exceeds {limit} bytes"
                    )
                chunks.append(data)
            payload = b"".join(chunks)
            if hasattr(response, "_content"):
                response._content = payload
                response._content_consumed = True
            return payload
    content = getattr(response, "content", None)
    if isinstance(content, (bytes, bytearray)):
        payload = bytes(content)
    else:
        text = getattr(response, "text", None)
        if not isinstance(text, str):
            return None
        payload = text.encode("utf-8")
    if len(payload) > limit:
        raise ResponseTooLargeError(f"response body exceeds {limit} bytes")
    return payload


def bounded_response_text(
    response: Any,
    *,
    limit: int = DEFAULT_RESPONSE_LIMIT,
) -> str:
    payload = bounded_response_bytes(response, limit=limit)
    if payload is None:
        text = getattr(response, "text", None)
        if not isinstance(text, str):
            raise ValueError("response does not expose a text body")
        return text
    encoding = str(getattr(response, "encoding", None) or "utf-8")
    try:
        return payload.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")
