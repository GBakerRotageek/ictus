#!/usr/bin/env python3
"""Attach to a live Conductor run, answer its gates, and leave before it reaps.

The smoke test behind docs/run-events.md. Standard library only, deliberately:
the point is that anyone can run it against a live engine without installing
anything, and the real implementation's dependency choice stays open.

    python3 docs/smoke/subscribe.py                       # approve everything
    python3 docs/smoke/subscribe.py smoke_gate=rejected:no thanks

Each argument is `<agent>=<value>` with an optional `:<free text>` for a choice
that declares `prompt_for`. Any gate not named is answered with its first option.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import pathlib
import secrets
import socket
import struct
import sys
import urllib.request

RUNS = pathlib.Path.home() / ".conductor" / "runs"
TERMINAL = ("workflow_completed", "workflow_failed")


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


def live_run() -> dict:
    """The most recently started run still serving a dashboard.

    Records are moved to `terminal/` when a run reaps, so anything still in
    `runs/` is live and no staleness filter is needed.
    """
    records = []
    for path in RUNS.glob("*.json"):
        try:
            record = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("port"):
            records.append(record)
    if not records:
        sys.exit(f"no live run with a dashboard in {RUNS}. Start one first — see the README.")
    return max(records, key=lambda record: record["started_at"])


def token_for(port: int) -> str:
    """The dashboard token. Needed for the handshake, not for reading state."""
    override = os.environ.get("CONDUCTOR_GATE_TOKEN")
    if override:
        return override
    return (RUNS / f"dashboard-{port}.token").read_text().strip()


def history(port: int) -> list[dict]:
    """Everything emitted before we connected.

    The socket replays nothing, so without this a run already parked at a gate
    looks like a run that is simply quiet, and we would wait on it forever.
    """
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=10) as response:
        return json.loads(response.read())


# --------------------------------------------------------------------------- #
# Just enough WebSocket (RFC 6455) to avoid a dependency
# --------------------------------------------------------------------------- #


class Socket:
    """A text-frame WebSocket client. Client frames are masked; server's are not."""

    def __init__(self, host: str, port: int, path: str, headers: dict[str, str]) -> None:
        self._sock = socket.create_connection((host, port), timeout=30)
        self._buffer = b""
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        request = [
            f"GET {path} HTTP/1.1",
            f"Host: {host}:{port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
            *(f"{name}: {value}" for name, value in headers.items()),
        ]
        self._sock.sendall(("\r\n".join(request) + "\r\n\r\n").encode())
        status = self._read_until(b"\r\n\r\n").split(b"\r\n", 1)[0]
        if b"101" not in status:
            raise SystemExit(f"handshake refused: {status.decode(errors='replace')}")

    def _read_until(self, marker: bytes) -> bytes:
        while marker not in self._buffer:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("closed during handshake")
            self._buffer += chunk
        head, _, self._buffer = self._buffer.partition(marker)
        return head

    def _read_exactly(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("closed mid-frame")
            self._buffer += chunk
        taken, self._buffer = self._buffer[:count], self._buffer[count:]
        return taken

    def send(self, text: str) -> None:
        payload = text.encode()
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 1 << 16:
            header.append(0x80 | 126)
            header += struct.pack("!H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", length)
        mask = secrets.token_bytes(4)
        header += mask
        self._sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def messages(self):
        """Yield each text message. Answers pings; stops on close."""
        while True:
            first, second = self._read_exactly(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exactly(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exactly(8))[0]
            payload = self._read_exactly(length) if length else b""
            if second & 0x80:  # a server frame must not be masked
                payload = bytes(b ^ payload[i % 4] for i, b in enumerate(payload[4:]))
            if opcode == 0x8:
                return
            if opcode == 0x9:
                self._sock.sendall(b"\x8a\x80" + secrets.token_bytes(4))
                continue
            if opcode in (0x1, 0x0):
                yield payload.decode(errors="replace")

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._sock.sendall(b"\x88\x80" + secrets.token_bytes(4))
        self._sock.close()


# --------------------------------------------------------------------------- #


def parse(argv: list[str]) -> dict[str, tuple[str, str | None]]:
    answers: dict[str, tuple[str, str | None]] = {}
    for argument in argv:
        agent, _, rest = argument.partition("=")
        choice, separator, note = rest.partition(":")
        answers[agent] = (choice, note if separator else None)
    return answers


def main(argv: list[str]) -> int:
    answers = parse(argv)
    record = live_run()
    port = record["port"]
    out = pathlib.Path(f"smoke-events-{record['run_id']}.jsonl")
    seen: set[tuple[str, float]] = set()

    print(f"run {record['run_id']}  port {port}  workflow {record['workflow_name']}")
    connection = Socket("127.0.0.1", port, "/ws", {"Authorization": f"Bearer {token_for(port)}"})
    print("connected")

    with out.open("w") as log:

        def handle(event: dict, *, live: bool) -> bool:
            key = (event["type"], event["timestamp"])
            if key in seen:
                return False
            seen.add(key)
            log.write(json.dumps(event) + "\n")
            log.flush()
            kind = event["type"]
            print(f"  {'<-' if live else '..'} {kind}")
            if kind == "gate_presented":
                data = event["data"]
                agent = data["agent_name"]
                choice, note = answers.get(agent, (data["options"][0], None))
                message = {
                    "type": "gate_response",
                    "agent_name": agent,
                    "selected_value": choice,
                }
                if note is not None:
                    message["additional_input"] = note
                connection.send(json.dumps(message))
                print(f"  -> {agent} = {choice}" + (f"  note={note!r}" if note else ""))
            return kind in TERMINAL

        try:
            # Connected first, so nothing emitted between here and the seed is
            # lost; the dedupe on (type, timestamp) absorbs the overlap.
            for event in history(port):
                if handle(event, live=False):
                    break
            else:
                for raw in connection.messages():
                    if handle(json.loads(raw), live=True):
                        break
        finally:
            connection.close()

    print(f"\nclosed. {len(seen)} events -> {out}")
    print("the run reaps about 30s after this disconnect; nothing else holds it open.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
