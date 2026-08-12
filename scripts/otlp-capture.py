#!/usr/bin/env python3
"""Loopback-only OTLP/HTTP trace capture for local proof.

Binds 127.0.0.1 (or ::1) only. Writes raw request bodies for later protobuf
parse. This is not a collector backend and retains nothing beyond the proof
directory.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


class _CaptureServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], output_dir: Path):
        super().__init__(address, handler)
        self.output_dir = output_dir
        self.lock = threading.Lock()
        self.count = 0
        self.bytes_received = 0


def _handler(output_dir: Path) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server: _CaptureServer

        def log_message(self, format: str, *args: Any) -> None:
            sys.stderr.write("otlp-capture: " + (format % args) + "\n")

        def do_GET(self) -> None:
            if self.path.split("?", 1)[0] != "/healthz":
                self.send_error(404)
                return
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            path = self.path.split("?", 1)[0]
            if path not in {"/v1/traces", "/v1/traces/"}:
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            encoding = (self.headers.get("Content-Encoding") or "").lower()
            if encoding == "gzip":
                raw = gzip.decompress(raw)
            with self.server.lock:
                self.server.count += 1
                self.server.bytes_received += len(raw)
                target = output_dir / f"export-{self.server.count:04d}.pb"
                target.write_bytes(raw)
                (output_dir / "capture-meta.json").write_text(
                    json.dumps(
                        {
                            "requests": self.server.count,
                            "bytes_received": self.server.bytes_received,
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
            response = b""
            try:
                from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
                    ExportTraceServiceResponse,
                )

                response = ExportTraceServiceResponse().SerializeToString()
            except Exception:
                response = b""
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Loopback OTLP/HTTP trace capture")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)

    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("otlp-capture refuses non-loopback bind", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    server = _CaptureServer((args.host, args.port), _handler(output_dir), output_dir)
    host, port = server.server_address[:2]
    listen = {"host": host, "port": port}
    (output_dir / "listen.json").write_text(json.dumps(listen) + "\n", encoding="utf-8")
    print(f"OTLP_CAPTURE_LISTEN={host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
