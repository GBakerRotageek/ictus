#!/usr/bin/env python3
"""Stands in for a Slack incoming webhook, so you can see a report without one.

Slack's incoming webhooks take a JSON POST over ordinary HTTPS and nothing else,
so anything that accepts a POST will do — which means the delivery path can be
watched end to end before anyone creates a Slack app.

    python3 docs/smoke/fake_channel.py
    export SMOKE_WEBHOOK_URL=http://127.0.0.1:8723/not-a-real-hook

Then add a notifier to a pipeline and run `ictus watch <folder>`. See the README.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8723


class Channel(BaseHTTPRequestHandler):
    """Prints the message Slack would have rendered."""

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"text": raw.decode(errors="replace")}
        print("\n--- a message arrived ---")
        print(body.get("text") or json.dumps(body, indent=2))
        sys.stdout.flush()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_: object) -> None:
        """Quiet: the message above is the output worth reading."""


if __name__ == "__main__":
    print(f"listening on http://127.0.0.1:{PORT} — ctrl-c to stop")
    HTTPServer(("127.0.0.1", PORT), Channel).serve_forever()
