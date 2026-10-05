"""Just enough of RFC 6455 to hold one conversation with a dashboard.

A dependency was the obvious alternative and was rejected twice over. ``ictus``
is synchronous end to end — a Typer CLI driving subprocesses — and the
maintained clients are asyncio-first, so taking one means an event loop in the
CLI to carry sixty lines of framing. And the surface actually needed is narrow:
one connection, text frames, no extensions, no compression negotiation, no
fragmentation to originate. The repository's standing rule is to prefer the
standard library and justify each dependency; this one could not be justified.

What that costs is written down rather than discovered: anything beyond the
subset below is unimplemented, and ``messages`` raises rather than guessing.

Verified against conductor-cli 0.1.41's dashboard.
"""

from __future__ import annotations

import base64
import contextlib
import secrets
import socket
import ssl
import struct
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from ictus.errors import IctusError

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

__all__ = ["HandshakeError", "WebSocket", "connect"]

_FIN_TEXT = 0x81
_MASKED = 0x80

_CONTINUATION, _TEXT, _BINARY, _CLOSE, _PING, _PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA

#: Bounds reaching the dashboard and finishing the handshake, nothing after.
CONNECT_TIMEOUT_SECONDS = 15.0


class HandshakeError(IctusError):
    """The dashboard refused the connection, usually a token the run won't take."""


class WebSocket:
    """One text-frame conversation. Not thread-safe; one per thread."""

    def __init__(
        self,
        host: str,
        port: int,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = CONNECT_TIMEOUT_SECONDS,
        tls: bool = False,
    ) -> None:
        self._sock = socket.create_connection((host, port), timeout=timeout)
        if tls:
            # Slack's Socket Mode is wss. The default context verifies the
            # certificate and the hostname, which is the point of using it.
            self._sock = ssl.create_default_context().wrap_socket(self._sock, server_hostname=host)
        self._buffer = b""
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {host}:{port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
            *(f"{name}: {value}" for name, value in (headers or {}).items()),
        ]
        self._sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        status = self._until(b"\r\n\r\n").split(b"\r\n", 1)[0].decode(errors="replace")
        if " 101" not in status:
            self.close()
            raise HandshakeError(
                f"{host}:{port}{path} refused the connection: {status}. "
                "A 403 is the token; read-only routes take none, the socket does."
            )
        # Blocking from here on, deliberately. A run parked at a gate emits
        # nothing for as long as the person takes to answer it, so any read
        # deadline drops precisely the connection that was worth holding — and
        # the gate it was waiting to report stays unreported.
        self._sock.settimeout(None)

    # -- framing ------------------------------------------------------------

    def _until(self, marker: bytes) -> bytes:
        while marker not in self._buffer:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("closed during handshake")
            self._buffer += chunk
        head, _, self._buffer = self._buffer.partition(marker)
        return head

    def _exactly(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("closed mid-frame")
            self._buffer += chunk
        taken, self._buffer = self._buffer[:count], self._buffer[count:]
        return taken

    def send(self, text: str) -> None:
        """Send one text frame. A client frame must be masked; a server's is not."""
        payload = text.encode()
        header = bytearray([_FIN_TEXT])
        size = len(payload)
        if size < 126:
            header.append(_MASKED | size)
        elif size < 1 << 16:
            header.append(_MASKED | 126)
            header += struct.pack("!H", size)
        else:
            header.append(_MASKED | 127)
            header += struct.pack("!Q", size)
        mask = secrets.token_bytes(4)
        header += mask
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def messages(self) -> Iterator[str]:
        """Each text message, until the peer closes. Answers pings in passing."""
        pending = ""
        while True:
            first, second = self._exactly(2)
            final, opcode = bool(first & 0x80), first & 0x0F
            size = second & 0x7F
            if size == 126:
                size = struct.unpack("!H", self._exactly(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self._exactly(8))[0]
            payload = self._exactly(size) if size else b""
            if second & _MASKED:  # a server frame must not be masked
                payload = bytes(b ^ payload[i % 4] for i, b in enumerate(payload[4:]))

            if opcode == _CLOSE:
                return
            if opcode == _PING:
                self._sock.sendall(bytes([0x80 | _PONG, _MASKED]) + secrets.token_bytes(4))
                continue
            if opcode == _PONG:
                continue
            if opcode == _BINARY:
                raise NotImplementedError(
                    "the dashboard sent a binary frame; this client is text-only"
                )
            if opcode not in (_TEXT, _CONTINUATION):
                raise NotImplementedError(f"unsupported websocket opcode {opcode:#x}")

            # The dashboard does not fragment today. Reassembling anyway is
            # cheaper than a parser that silently truncates if it ever does.
            pending += payload.decode(errors="replace")
            if final:
                yield pending
                pending = ""

    def close(self) -> None:
        """Say goodbye and hang up. Safe to call twice."""
        with contextlib.suppress(OSError):
            self._sock.sendall(bytes([0x80 | _CLOSE, _MASKED]) + secrets.token_bytes(4))
        with contextlib.suppress(OSError):
            self._sock.close()

    def __enter__(self) -> WebSocket:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def connect(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = CONNECT_TIMEOUT_SECONDS,
) -> WebSocket:
    """Open ``ws://`` or ``wss://``, with the query string kept.

    Slack hands out a URL with credentials in its query, so dropping it would
    produce a handshake that is refused for no visible reason.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("ws", "wss"):
        raise HandshakeError(f"{parts.scheme!r} is not a websocket scheme; use ws or wss")
    tls = parts.scheme == "wss"
    port = parts.port or (443 if tls else 80)
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return WebSocket(
        parts.hostname or "127.0.0.1", port, path, headers=headers, timeout=timeout, tls=tls
    )
