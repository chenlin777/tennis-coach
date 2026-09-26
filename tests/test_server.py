"""The local static server must never expose source videos or repository data."""

from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import threading
import unittest

from serve import EditorHandler


class StaticServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), EditorHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, path, method="GET", headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_static_assets_and_no_external_connections_policy(self):
        for path in (
            "/", "/index.html", "/app.js", "/styles.css", "/auto_privacy.js",
            "/forehand.html", "/forehand.js", "/forehand.css", "/pose_analysis.js", "/arm_rules.js",
        ):
            with self.subTest(path=path):
                status, headers, body = self.request(path)
                self.assertEqual(status, 200)
                self.assertTrue(body)
                policy = headers["Content-Security-Policy"]
                directives = {parts[0]: parts[1:] for rule in policy.split(";") if (parts := rule.split())}
                self.assertEqual(directives["connect-src"], ["'self'"])
                self.assertEqual(set(directives["script-src"]), {"'self'", "'wasm-unsafe-eval'"})
                self.assertEqual(directives["default-src"], ["'none'"])
                self.assertEqual(directives["form-action"], ["'none'"])
                self.assertEqual(headers["Cache-Control"], "no-store")

    def test_private_paths_and_traversal_are_not_served(self):
        for path in (
            "/local/videos/wall_practice.mp4", "/.git/config", "/README.md", "/serve.py",
            "/../local/videos/wall_practice.mp4", "/%2e%2e/local/", "/web/../local/",
            "/vendor/", "/vendor/models/", "/vendor/models/../../local/videos/wall_practice.mp4",
            "/vendor/models/not-allowlisted.tflite", "/vendor/mediapipe/../README.md",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)

    def test_no_upload_endpoint(self):
        self.assertEqual(self.request("/", method="POST")[0], 405)

    def test_rejects_foreign_host(self):
        self.assertEqual(self.request("/", headers={"Host": "example.com"})[0], 403)

    def test_head_has_no_body(self):
        status, headers, body = self.request("/", method="HEAD")
        self.assertEqual(status, 200)
        self.assertGreater(int(headers["Content-Length"]), 0)
        self.assertEqual(body, b"")


if __name__ == "__main__":
    unittest.main()
