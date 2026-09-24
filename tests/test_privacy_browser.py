"""Exercise privacy processing with synthetic media, never personal videos.

Install requirements-dev.txt and run:
    python -m unittest discover -s tests -p test_privacy_browser.py -v

Uses an installed Edge/Chrome, or TENNIS_TEST_BROWSER. No browser download.
Generated fixtures and downloads stay in ignored local/outputs/privacy-test/.
"""

from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import subprocess
import threading
import unittest
from urllib.parse import urlsplit

try:
    import imageio_ffmpeg
    from playwright.sync_api import expect, sync_playwright
except ImportError as exc:
    raise unittest.SkipTest("Browser tests need requirements-dev.txt") from exc

from serve import EditorHandler


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "local" / "outputs" / "privacy-test"
WIDTH, HEIGHT = 640, 360
MASK_RGB = (38, 56, 50)


def browser_executable():
    candidates = [os.environ.get("TENNIS_TEST_BROWSER")]
    for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES")):
        if base:
            candidates.extend([
                str(Path(base) / "Microsoft/Edge/Application/msedge.exe"),
                str(Path(base) / "Google/Chrome/Application/chrome.exe"),
            ])
    candidates.extend(shutil.which(name) for name in ("microsoft-edge", "google-chrome", "chromium"))
    return next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), None)


class BrowserTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        executable = browser_executable()
        if not executable:
            raise unittest.SkipTest("Install Edge/Chrome or set TENNIS_TEST_BROWSER; no download is attempted")
        OUTPUT.mkdir(parents=True, exist_ok=True)
        cls.ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        cls.fixture = OUTPUT / "synthetic-with-audio.mp4"
        subprocess.run([
            cls.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30,drawgrid=width=17:height=19:thickness=2:color=white@0.6",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-movflags", "+faststart", str(cls.fixture),
        ], check=True, capture_output=True, timeout=30)
        cls.second_fixture = OUTPUT / "synthetic-second.mp4"
        shutil.copyfile(cls.fixture, cls.second_fixture)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), EditorHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.stop_server)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        cls.browser = cls.playwright.chromium.launch(executable_path=executable, headless=True)
        cls.addClassCleanup(cls.browser.close)
        cls.address = f"http://127.0.0.1:{cls.server.server_port}/"

    @classmethod
    def stop_server(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1000, "height": 1000}, accept_downloads=True)
        self.page = self.context.new_page()
        self.page.set_default_timeout(10000)
        self.page_errors = []
        self.requests = []
        self.page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        self.context.on("request", lambda request: self.requests.append((request.method, request.url)))
        self.page.goto(self.address)

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.page_errors, [], "Browser JavaScript must not fail")
        for method, address in self.requests:
            parsed = urlsplit(address)
            if parsed.scheme in ("blob", "data"):
                continue
            if parsed.scheme in ("edge", "chrome"):
                # Edge's internal downloads UI and file icons are local browser resources.
                continue
            self.assertEqual(parsed.scheme, "http", address)
            self.assertEqual(parsed.hostname, "127.0.0.1", address)
            self.assertEqual(parsed.port, self.server.server_port, address)
            self.assertEqual(method, "GET", f"Unexpected upload or mutation request: {method} {address}")

    def load(self, path=None):
        self.page.locator("#videoFile").set_input_files(str(path or self.fixture))
        self.page.wait_for_function("""() => {
            const video = document.getElementById('sourceVideo');
            return video.readyState >= 2 && Number.isFinite(video.duration)
                && video.duration > 0 && !document.getElementById('playPause').disabled;
        }""")
        expect(self.page.locator("#fileMeta")).to_contain_text("640 × 360")
        brightness = self.page.locator("#videoCanvas").evaluate("""canvas => {
            const pixels = canvas.getContext('2d').getImageData(0, 0, 640, 360).data;
            let sum = 0, samples = 0;
            for (let i = 0; i < pixels.length; i += 400) {
                sum += pixels[i] + pixels[i + 1] + pixels[i + 2]; samples += 3;
            }
            return sum / samples;
        }""")
        self.assertGreater(brightness, 20, "Synthetic video's initial preview must be visible before seeking or playing")

    def draw(self, button, rect):
        self.page.locator(button).click()
        overlay = self.page.locator("#overlayCanvas")
        overlay.scroll_into_view_if_needed()
        bounds = overlay.bounding_box()
        self.assertIsNotNone(bounds)
        self.assertGreater(abs(bounds["width"] - WIDTH), 5, "Test must exercise CSS-scaled drawing")
        x, y, width, height = rect
        self.page.mouse.move(bounds["x"] + x * bounds["width"], bounds["y"] + y * bounds["height"])
        self.page.mouse.down()
        self.page.mouse.move(bounds["x"] + (x + width) * bounds["width"], bounds["y"] + (y + height) * bounds["height"], steps=6)
        self.page.mouse.up()

    def select_masks(self):
        self.page.locator('input[name="backgroundMode"][value="keep"]').check()
        self.draw("#drawKeep", (.25, .2, .6, .7))
        self.page.locator('input[name="faceMode"][value="manual"]').check()
        self.draw("#drawFace", (.35, .3, .15, .2))

    def download_export(self, filename):
        self.page.locator("#exportVideo").click()
        expect(self.page.locator("#downloadVideo")).to_be_visible(timeout=15000)
        with self.page.expect_download() as pending:
            self.page.locator("#downloadVideo").click()
        download = pending.value
        self.assertIsNone(download.failure())
        output = OUTPUT / filename
        download.save_as(str(output))
        self.assertGreater(output.stat().st_size, 1000)
        return output

    def assert_silent_masked_export(self, path):
        # The fixture has AAC, so absence of audio in the result tests removal.
        source_info = subprocess.run([self.ffmpeg, "-hide_banner", "-i", str(self.fixture)], capture_output=True, text=True, timeout=15)
        self.assertIn("Audio: aac", source_info.stderr)
        exported_info = subprocess.run([self.ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=15)
        self.assertIn("Video:", exported_info.stderr)
        self.assertNotIn("Audio:", exported_info.stderr)
        decoded = subprocess.run([
            self.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-vf", "fps=10", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ], check=True, capture_output=True, timeout=30).stdout
        frame_size = WIDTH * HEIGHT * 3
        self.assertEqual(len(decoded) % frame_size, 0)
        count = len(decoded) // frame_size
        self.assertGreaterEqual(count, 15, "Export should include the full two-second fixture")
        self.assertLessEqual(count, 30)
        for index in (0, count // 2, count - 1):
            with self.subTest(export_frame=index):
                frame = memoryview(decoded)[index * frame_size:(index + 1) * frame_size]
                for y in range(122, 167, 8):
                    for x in range(240, 303, 8):
                        offset = (y * WIDTH + x) * 3
                        actual = frame[offset:offset + 3]
                        self.assertLessEqual(max(abs(actual[c] - MASK_RGB[c]) for c in range(3)), 10,
                                             f"Mask missing in frame {index} at ({x}, {y}): {list(actual)}")

class PrivacyBrowserTests(BrowserTestCase):
    def test_privacy_workflow_pixels_export_cancel_and_reset(self):
        for selector in ("#playPause", "#timeline", "#exportVideo"):
            expect(self.page.locator(selector)).to_be_disabled()
        for selector in ("#faceOptions", "#backgroundOptions"):
            expect(self.page.locator(selector)).to_have_attribute("disabled", "")
        self.load()
        self.assertAlmostEqual(self.page.locator("#sourceVideo").evaluate("video => video.duration"), 2, delta=.1)

        self.page.locator('input[name="faceMode"][value="manual"]').check()
        self.page.locator("#exportVideo").click()
        expect(self.page.locator("#status")).to_contain_text("请先画至少一个遮盖区")
        expect(self.page.locator("#downloadVideo")).to_be_hidden()
        self.page.locator('input[name="faceMode"][value="none"]').check()
        self.page.locator('input[name="backgroundMode"][value="keep"]').check()
        self.page.locator("#exportVideo").click()
        expect(self.page.locator("#status")).to_contain_text("请先画保留区")
        expect(self.page.locator("#downloadVideo")).to_be_hidden()

        self.select_masks()
        stats = self.page.evaluate("""() => {
            const output = document.getElementById('videoCanvas');
            const source = document.createElement('canvas');
            source.width = output.width; source.height = output.height;
            source.getContext('2d').drawImage(document.getElementById('sourceVideo'), 0, 0);
            const a = output.getContext('2d').getImageData(0, 0, 640, 360).data;
            const b = source.getContext('2d').getImageData(0, 0, 640, 360).data;
            let faceMismatch = 0, keptMismatch = 0, outsideChanged = 0, outsideCount = 0;
            for (let y = 0; y < 360; y += 2) for (let x = 0; x < 640; x += 2) {
                const i = (y * 640 + x) * 4;
                const changed = Math.max(Math.abs(a[i] - b[i]), Math.abs(a[i+1] - b[i+1]), Math.abs(a[i+2] - b[i+2]));
                if (x > 236 && x < 309 && y > 120 && y < 171)
                    faceMismatch += Number(a[i] !== 38 || a[i+1] !== 56 || a[i+2] !== 50);
                if (x > 170 && x < 530 && y > 82 && y < 312 && !(x > 214 && x < 330 && y > 98 && y < 190))
                    keptMismatch += Number(changed > 0);
                if (x < 148 || x > 558 || y < 60 || y > 334) {
                    outsideCount++; outsideChanged += Number(changed > 5);
                }
            }
            return {faceMismatch, keptMismatch, outsideChanged, outsideCount};
        }""")
        self.page.screenshot(path=str(OUTPUT / "editor.png"), full_page=True)
        self.assertEqual(stats["faceMismatch"], 0, stats)
        self.assertEqual(stats["keptMismatch"], 0, stats)
        self.assertGreater(stats["outsideChanged"] / stats["outsideCount"], .05, stats)

        self.page.locator("#playbackRate").select_option("0.5")
        self.page.locator("#timeline").evaluate("el => { el.value = '0.6'; el.dispatchEvent(new Event('input')); }")
        self.page.wait_for_function("() => Math.abs(document.getElementById('sourceVideo').currentTime - .6) < .03 && !document.getElementById('sourceVideo').seeking")
        self.page.locator("#exportVideo").click()
        expect(self.page.locator("#cancelExport")).to_be_visible()
        for selector in ("#videoFile", "#playPause", "#timeline", "#playbackRate", "#exportVideo"):
            expect(self.page.locator(selector)).to_be_disabled()
        for selector in ("#faceOptions", "#backgroundOptions"):
            expect(self.page.locator(selector)).to_have_attribute("disabled", "")
        self.page.locator("#cancelExport").click()
        expect(self.page.locator("#status")).to_contain_text("已取消")
        expect(self.page.locator("#downloadVideo")).to_be_hidden()
        expect(self.page.locator("#exportVideo")).to_be_enabled()
        self.page.wait_for_function("() => Math.abs(document.getElementById('sourceVideo').currentTime - .6) < .03")
        self.assertEqual(self.page.locator("#sourceVideo").evaluate("video => video.playbackRate"), .5)

        output = self.download_export("masked-silent.webm")
        self.assert_silent_masked_export(output)

        self.load(self.second_fixture)
        expect(self.page.locator("#faceCount")).to_have_text("尚未画遮盖区")
        expect(self.page.locator("#keepStatus")).to_have_text("尚未画保留区")
        expect(self.page.locator("#downloadVideo")).to_be_hidden()
        self.page.locator('input[name="faceMode"][value="none"]').check()
        self.page.locator('input[name="backgroundMode"][value="none"]').check()
        self.download_export("unmasked-silent.webm")

    def test_exported_webm_can_be_imported_again(self):
        self.load()
        self.select_masks()
        output = self.download_export("round-trip-silent.webm")
        self.load(output)
        expect(self.page.locator("#playPause")).to_be_enabled()
        expect(self.page.locator("#exportVideo")).to_be_enabled()
        self.assertAlmostEqual(self.page.locator("#sourceVideo").evaluate("video => video.duration"), 2, delta=.5)


if __name__ == "__main__":
    unittest.main()
