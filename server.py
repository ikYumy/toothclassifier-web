#!/usr/bin/env python3
"""
ToothClassifier Local Server
=============================
Serves the web UI + test set files over HTTP so you can use it from
your phone or any device on the same network.

Usage:
    python server.py                          # default port 8765
    python server.py --port 8080              # custom port
    python server.py --testset "D:\\data"     # custom test set path

Then open http://<YOUR_COMPUTER_IP>:8765 on your phone.
"""

import argparse
import http.server
import json
import os
import re
import struct
import sys
from pathlib import Path

# ── Configuration ──────────────────────────────────────────
PORT = 8765
TEST_SET = r"E:\裁剪后数据-400-1000nm\test set"
WEB_ROOT = Path(__file__).resolve().parent  # toothclassifier-web dir


def find_hdr_files(root: str) -> list[dict]:
    """Scan test set directory for HDR + binary pairs."""
    results = []
    root_path = Path(root)
    if not root_path.exists():
        return results

    for subdir in sorted(root_path.iterdir()):
        if not subdir.is_dir():
            continue
        # Find HDR files (case-insensitive: .HDR, .hdr)
        hdr_files = list(subdir.glob("*.HDR")) + list(subdir.glob("*.hdr"))
        if not hdr_files:
            continue
        hdr_path = hdr_files[0]
        stem = hdr_path.stem  # e.g. "09-1-1"

        # Find binary data file: same stem, no extension
        bin_path = subdir / stem
        if not bin_path.exists():
            continue

        # Parse HDR for metadata
        info = parse_hdr_summary(hdr_path)
        info["name"] = stem
        info["subdir"] = subdir.name
        info["hdr_file"] = hdr_path.name
        info["bin_file"] = stem
        info["hdr_size"] = hdr_path.stat().st_size
        info["bin_size"] = bin_path.stat().st_size
        results.append(info)

    return results


def parse_hdr_summary(path: Path) -> dict:
    """Extract key fields from ENVI HDR file."""
    info = {}
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip().lower()
        val = val.strip().strip("{}").strip()
        info[key] = val
    return {
        "samples": int(info.get("samples", 0)),
        "lines": int(info.get("lines", 0)),
        "bands": int(info.get("bands", 0)),
        "data_type": int(info.get("data type", "4")),
        "interleave": info.get("interleave", "bil"),
    }


class ToothClassifierHandler(http.server.SimpleHTTPRequestHandler):
    """Custom handler: static files + API endpoints."""

    test_set_root = TEST_SET

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def log_message(self, fmt, *args):
        # Compact log
        print(f"  {args[0]}")

    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors()
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]

        # ── API: list test images ──
        if path == "/api/list":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors()
            self.end_headers()
            files = find_hdr_files(self.test_set_root)
            self.wfile.write(json.dumps(files, ensure_ascii=False).encode())
            return

        # ── API: serve a file from test set ──
        if path.startswith("/api/file"):
            # /api/file?subdir=09-1-1&file=09-1-1.HDR
            qs = self.path.split("?", 1)[1] if "?" in self.path else ""
            params = {}
            for p in qs.split("&"):
                if "=" in p:
                    k, v = p.split("=", 1)
                    params[k] = v
            subdir = params.get("subdir", "")
            filename = params.get("file", "")

            # Security: prevent path traversal
            if ".." in subdir or ".." in filename or "/" in subdir or "\\" in subdir:
                self.send_error(403, "Forbidden")
                return

            filepath = Path(self.test_set_root) / subdir / filename
            if not filepath.resolve().is_relative_to(Path(self.test_set_root).resolve()):
                self.send_error(403, "Forbidden")
                return

            if not filepath.exists():
                self.send_error(404, f"File not found: {filename}")
                return

            # Determine content type
            ext = filepath.suffix.lower()
            if ext in (".hdr",):
                ct = "text/plain; charset=utf-8"
            elif ext in (".json",):
                ct = "application/json"
            else:
                ct = "application/octet-stream"

            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(filepath.stat().st_size))
            self.send_cors()
            self.end_headers()

            with open(filepath, "rb") as f:
                self.wfile.write(f.read())
            return

        # ── Default: serve static file ──
        return super().do_GET()


def get_local_ip() -> str:
    """Get the local network IP address."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    parser = argparse.ArgumentParser(description="ToothClassifier Local Server")
    parser.add_argument("--port", type=int, default=PORT, help=f"Port (default: {PORT})")
    parser.add_argument(
        "--testset",
        type=str,
        default=TEST_SET,
        help="Path to test set directory",
    )
    args = parser.parse_args()

    ToothClassifierHandler.test_set_root = args.testset

    if not Path(args.testset).exists():
        print(f"\n⚠️  WARNING: Test set directory not found:")
        print(f"   {args.testset}")
        print(f"   Server will start but no test images will be available.\n")
    else:
        files = find_hdr_files(args.testset)
        print(f"\n📁 Test set: {len(files)} images found in {args.testset}")
        for f in files:
            print(f"   {f['name']}  ({f['samples']}×{f['lines']}×{f['bands']} bands, "
                  f"{f['bin_size']/1024/1024:.1f} MB)")

    ip = get_local_ip()
    print(f"\n{'='*55}")
    print(f"  🦷 ToothClassifier Server")
    print(f"  {'='*51}")
    print(f"  Local:   http://localhost:{args.port}")
    print(f"  Network: http://{ip}:{args.port}")
    print(f"\n  Open the Network URL on your phone 📱")
    print(f"  Press Ctrl+C to stop")
    print(f"{'='*55}\n")

    server = http.server.HTTPServer(("0.0.0.0", args.port), ToothClassifierHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Server stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
