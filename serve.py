"""Serve only the privacy editor's static files on this computer.

No video upload endpoint, repository browsing, or third-party packages.
"""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import webbrowser


WEB_ROOT = Path(__file__).resolve().parent / "web"
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/auto_privacy.js": ("auto_privacy.js", "text/javascript; charset=utf-8"),
}
VENDOR_ASSETS = {
    "/vendor/mediapipe/vision_bundle.mjs": ("vendor/mediapipe/vision_bundle.mjs", "text/javascript; charset=utf-8"),
    "/vendor/models/blaze_face_full_range.tflite": ("vendor/models/blaze_face_full_range.tflite", "application/octet-stream"),
    "/vendor/models/pose_landmarker_lite.task": ("vendor/models/pose_landmarker_lite.task", "application/octet-stream"),
}
for wasm_name in ("vision_wasm_internal", "vision_wasm_nosimd_internal", "vision_wasm_module_internal"):
    for extension, content_type in (("js", "text/javascript; charset=utf-8"), ("wasm", "application/wasm")):
        relative = f"vendor/mediapipe/wasm/{wasm_name}.{extension}"
        VENDOR_ASSETS[f"/{relative}"] = (relative, content_type)
CSP = (
    "default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; "
    "img-src 'self' data: blob:; media-src blob:; connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
)


class EditorHandler(BaseHTTPRequestHandler):
    def _respond(self, status, body=b"", content_type="text/plain; charset=utf-8", head=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _serve(self, head=False):
        port = self.server.server_port
        if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            self._respond(403, b"Local access only.", head=head)
            return
        try:
            request_url = urlsplit(self.path)
        except ValueError:
            self._respond(400, b"Invalid path.", head=head)
            return
        if request_url.scheme or request_url.netloc:
            self._respond(400, b"Relative paths only.", head=head)
            return
        asset = ASSETS.get(request_url.path) or VENDOR_ASSETS.get(request_url.path)
        if asset is None:
            self._respond(404, b"Not found.", head=head)
            return
        name, content_type = asset
        try:
            body = (WEB_ROOT / name).read_bytes()
        except OSError:
            self._respond(404, b"Asset not found.", head=head)
            return
        self._respond(200, body, content_type, head=head)

    def do_GET(self):
        self._serve()

    def do_HEAD(self):
        self._serve(head=True)

    def do_POST(self):
        self._respond(405, b"This application has no upload endpoint.")

    def log_message(self, format, *args):
        # Keep private filenames and arbitrary request paths out of logs.
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Open the local editor in a browser")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), EditorHandler)
    except OSError as exc:
        parser.exit(1, f"Cannot start the local editor: {exc}\nTry another port with --port 8766.\n")
    address = f"http://127.0.0.1:{args.port}/"
    print(f"Tennis Coach privacy editor: {address}", flush=True)
    print("Files are processed in your browser. Press Ctrl+C to stop.", flush=True)
    if args.open:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
