"""Forehand UI lifecycle tests using synthetic video and controlled pose results.

The intercepted pose module is deliberately NOT a model. These tests exercise
file isolation, cancellation, report provenance, and playback. Detection quality
needs separate checks against real videos; no personal video is used here.
"""

import base64
import json
import subprocess

import test_privacy_browser as browser_helpers
from playwright.sync_api import expect


FAKE_POSE_MODULE = r"""
export const MODEL_VERSION = 'synthetic-ui-test-not-a-model';
export const MAX_DURATION = 60;
export function disposePoseModel() {}
export function scanPoses(file, {signal, onProgress, region}) {
  onProgress({phase:'scanning', fraction:.25, time:.5, duration:2});
  return new Promise((resolve, reject) => {
    window.__pendingPoseScans ??= [];
    window.__pendingPoseScans.push({resolve, reject, onProgress, signal, region, fileName:file.name});
    // Intentionally ignore abort. Late callbacks must be rejected by the UI.
  });
}
"""


# Only the cache/provenance and navigation tests intercept rules. The empty-pose
# test below uses the real rules module. This fixture makes stale reports visible
# without depending on any coaching threshold or synthetic stroke definition.
FAKE_RULE_MODULE = r"""
export const RULE_VERSION = 'synthetic-ui-test-not-a-coaching-rule';
export const ARM_RULES_VERSION = RULE_VERSION;
export function analyzeArm(scan, options) {
  const optionsAtAnalysis = JSON.parse(JSON.stringify(options));
  window.__ruleCalls ??= [];
  window.__ruleCalls.push(optionsAtAnalysis);
  return {
    status:'candidates', optionsAtAnalysis,
    candidates:[{id:'synthetic-candidate', start:.7, end:1.2, time:.75,
      reason:'Synthetic UI navigation fixture', suggestion:'Synthetic test only',
      confidence:'tentative', evidence:{phase:'前挥候选',sampleTimes:[.75,1]}}],
    reviewIntervals:[{start:1.4,end:1.8,reason:'Synthetic missing interval'}],
    trackedFrames:[], summary:{totalFrames:25,usableFrames:20,coverage:.8},
    limitations:['Synthetic rules do not assess technique.'],
  };
}
"""


class ForehandBrowserTests(browser_helpers.BrowserTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.long_streaming_fixture = browser_helpers.OUTPUT / "synthetic-long-streaming.webm"
        subprocess.run([
            cls.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=2", "-t", "61",
            "-c:v", "libvpx", "-deadline", "realtime", "-cpu-used", "8",
            "-an", "-f", "webm", "-live", "1", str(cls.long_streaming_fixture),
        ], check=True, capture_output=True, timeout=30)

    def setUp(self):
        super().setUp()
        self.page.route("**/pose_analysis.js", lambda route: route.fulfill(
            status=200, content_type="text/javascript; charset=utf-8", body=FAKE_POSE_MODULE))
        self.page.goto(self.address + "forehand.html")

    def use_fake_rules(self):
        self.page.route("**/arm_rules.js", lambda route: route.fulfill(
            status=200, content_type="text/javascript; charset=utf-8", body=FAKE_RULE_MODULE))
        self.page.reload()

    def load(self, path=None):
        self.page.locator("#videoFile").set_input_files(str(path or self.fixture))
        self.page.wait_for_function("""() => {
            const video = document.getElementById('sourceVideo');
            return video.readyState >= 2 && Number.isFinite(video.duration)
                && video.duration > 0 && !document.getElementById('playPause').disabled;
        }""")
        expect(self.page.locator("#fileName")).to_have_text((path or self.fixture).name)

    def start_scan(self, count=1):
        self.page.locator("#analyzeButton").click()
        self.page.wait_for_function("count => window.__pendingPoseScans?.length === count", arg=count)

    def finish_scan(self, index=0, frames=None):
        self.page.evaluate("""({index, frames}) => {
            const scan = window.__pendingPoseScans[index];
            scan.onProgress({phase:'scanning', fraction:1, time:2, duration:2});
            scan.resolve({frames, duration:2, width:640, height:360, sampleFps:12,
                modelVersion:'synthetic-ui-test-not-a-model', sampledWidth:640, sampledHeight:360,
                samplingRegion:{requested:scan.region, originalWidth:640, originalHeight:360}});
        }""", {"index": index, "frames": frames or []})

    def download_report(self, name):
        with self.page.expect_download() as pending:
            self.page.locator("#exportReport").click()
        download = pending.value
        self.assertIsNone(download.failure())
        path = browser_helpers.OUTPUT / name
        download.save_as(str(path))
        return json.loads(path.read_text(encoding="utf-8"))

    def test_cancel_and_file_switch_reject_late_scan_results(self):
        expect(self.page.locator("#exportReport")).to_be_disabled()
        self.load()
        self.start_scan()
        expect(self.page.locator("#exportReport")).to_be_disabled()
        self.page.locator("#cancelAnalysis").click()
        self.assertTrue(self.page.evaluate("() => window.__pendingPoseScans[0].signal.aborted"))
        status = self.page.locator("#analysisStatus").inner_text()
        self.finish_scan()
        self.page.wait_for_timeout(100)
        expect(self.page.locator("#exportReport")).to_be_disabled()
        expect(self.page.locator("#analysisStatus")).to_have_text(status)

        self.start_scan(2)
        self.load(self.second_fixture)
        self.assertTrue(self.page.evaluate("() => window.__pendingPoseScans[1].signal.aborted"))
        status = self.page.locator("#analysisStatus").inner_text()
        self.finish_scan(1)
        self.page.wait_for_timeout(100)
        expect(self.page.locator("#fileName")).to_have_text(self.second_fixture.name)
        expect(self.page.locator("#exportReport")).to_be_disabled()
        expect(self.page.locator("#analysisStatus")).to_have_text(status)
        expect(self.page.locator("#candidateList button")).to_have_count(0)

    def test_first_frame_is_visible_without_playing_or_seeking(self):
        for fixture in (self.fixture, self.second_fixture):
            with self.subTest(file=fixture.name):
                self.load(fixture)
                self.page.wait_for_function("""() => {
                    const canvas = document.getElementById('previewCanvas');
                    if (!canvas.width || !canvas.height) return false;
                    const pixels = canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
                    let sum=0, count=0;
                    for (let i=0; i<pixels.length; i+=400) {sum+=pixels[i]+pixels[i+1]+pixels[i+2];count+=3;}
                    return sum/count > 20;
                }""")
                self.assertEqual(self.page.locator("#sourceVideo").evaluate("v => v.currentTime"), 0)
                self.assertTrue(self.page.locator("#sourceVideo").evaluate("v => v.paused"))

    def test_option_change_during_scan_rejects_obsolete_result(self):
        self.load()
        self.start_scan()
        self.page.locator("#handedness").select_option("left")
        self.assertTrue(self.page.evaluate("() => window.__pendingPoseScans[0].signal.aborted"))
        status = self.page.locator("#analysisStatus").inner_text()
        self.finish_scan()
        self.page.wait_for_timeout(100)
        expect(self.page.locator("#exportReport")).to_be_disabled()
        expect(self.page.locator("#analysisStatus")).to_have_text(status)
        expect(self.page.locator("#analyzeButton")).to_be_enabled()

    def test_completed_scan_recomputes_options_and_target_without_rescanning(self):
        self.use_fake_rules()
        self.load()
        self.start_scan()
        self.finish_scan()
        expect(self.page.locator("#exportReport")).to_be_enabled()
        first = self.download_report("forehand-before-option-change.json")
        self.assertEqual(first["file"]["name"], self.fixture.name)
        self.assertTrue(first["requiresCoachReview"])
        self.assertEqual(first["modelVersion"], "synthetic-ui-test-not-a-model")

        self.page.locator("#handedness").select_option("left")
        self.page.locator("#practiceContext").select_option("self_feed")
        self.page.locator("#mirrored").check()
        self.page.locator("#timeline").evaluate("el => { el.value = '.6'; el.dispatchEvent(new Event('input')); }")
        self.page.wait_for_function("() => !document.getElementById('sourceVideo').seeking && Math.abs(document.getElementById('sourceVideo').currentTime - .6) < .03")
        self.page.locator("#chooseTarget").click()
        canvas = self.page.locator("#previewCanvas")
        canvas.scroll_into_view_if_needed()
        box = canvas.bounding_box()
        self.assertIsNotNone(box)
        self.page.mouse.click(box["x"] + .4 * box["width"], box["y"] + .45 * box["height"])
        expect(self.page.locator("#exportReport")).to_be_enabled()
        updated = self.download_report("forehand-current-options.json")
        options = updated["options"]
        self.assertEqual(options["handedness"], "left")
        self.assertEqual(options["context"], "self_feed")
        self.assertTrue(options["mirrored"])
        self.assertAlmostEqual(options["target"]["x"], .4, delta=.005)
        self.assertAlmostEqual(options["target"]["y"], .45, delta=.005)
        self.assertAlmostEqual(options["targetTime"], .6, delta=.03)
        for key in ("handedness", "context", "mirrored", "target", "targetTime"):
            self.assertEqual(updated["result"]["optionsAtAnalysis"][key], options[key], key)
        self.assertEqual(self.page.evaluate("() => window.__pendingPoseScans.length"), 1)

        self.page.locator("#clearTarget").click()
        cleared = self.download_report("forehand-target-cleared.json")
        self.assertIsNone(cleared["options"]["target"])
        self.assertIsNone(cleared["result"]["optionsAtAnalysis"]["target"])
        self.assertEqual(self.page.evaluate("() => window.__pendingPoseScans.length"), 1)
        self.load(self.second_fixture)
        expect(self.page.locator("#exportReport")).to_be_disabled()

    def set_region(self, x=10, y=15, width=70, height=65):
        self.page.locator('.region-inputs').evaluate("element => element.open=true")
        for field, value in zip(('regionX', 'regionY', 'regionWidth', 'regionHeight'), (x, y, width, height)):
            self.page.locator('#' + field).fill(str(value))
        self.page.locator('#applyRegion').click()

    def test_region_changes_invalidate_pose_cache_and_preserve_report_provenance(self):
        self.use_fake_rules()
        self.load()
        self.start_scan()
        self.set_region()
        self.assertTrue(self.page.evaluate('() => window.__pendingPoseScans[0].signal.aborted'))
        self.finish_scan()
        expect(self.page.locator('#exportReport')).to_be_disabled()
        self.start_scan(2)
        region = {'x': .1, 'y': .15, 'width': .7, 'height': .65}
        self.assertEqual(self.page.evaluate('() => window.__pendingPoseScans[1].region'), region)
        self.finish_scan(1)
        expect(self.page.locator('#exportReport')).to_be_enabled()
        report = self.download_report('forehand-region-report.json')
        self.assertEqual(report['options']['region'], region)
        self.assertEqual(report['samplingRegion']['requested'], region)
        self.assertEqual(report['samplingRegion']['originalWidth'], 640)
        self.set_region(15, 20, 60, 60)
        expect(self.page.locator('#exportReport')).to_be_disabled()
        self.page.locator('#handedness').select_option('right')
        expect(self.page.locator('#exportReport')).to_be_disabled()
        self.assertEqual(self.page.evaluate('() => window.__pendingPoseScans.length'), 2)
        self.start_scan(3)
        self.page.locator('#clearRegion').click()
        self.assertTrue(self.page.evaluate('() => window.__pendingPoseScans[2].signal.aborted'))
        self.finish_scan(2)
        expect(self.page.locator('#exportReport')).to_be_disabled()
        self.start_scan(4)
        self.assertIsNone(self.page.evaluate('() => window.__pendingPoseScans[3].region'))
        self.finish_scan(3)
        self.set_region()
        self.load(self.second_fixture)
        expect(self.page.locator('#clearRegion')).to_be_disabled()
        expect(self.page.locator('#regionWidth')).to_have_value('100')
        self.start_scan(5)
        self.assertIsNone(self.page.evaluate('() => window.__pendingPoseScans[4].region'))

    def test_region_drag_uses_picture_coordinates_and_click_does_not_select_person(self):
        self.use_fake_rules()
        self.load()
        canvas = self.page.locator('#previewCanvas')
        # A square element letterboxes the 16:9 video. Dragging in either
        # direction must map the displayed picture, not the outer element.
        canvas.evaluate("el => {el.style.width='400px'; el.style.height='400px';}")
        self.page.locator('#chooseRegion').click()
        canvas.scroll_into_view_if_needed()
        box = canvas.bounding_box()
        x0, y0 = box['x'], box['y'] + (400 - 225) / 2
        self.page.mouse.click(x0 + 200, y0 + 112.5)
        expect(self.page.locator('#clearTarget')).to_be_disabled()
        expect(self.page.locator('#clearRegion')).to_be_disabled()
        expect(self.page.locator('#chooseRegion')).to_have_attribute('aria-pressed', 'true')
        self.page.mouse.move(x0 + 320, y0 + 180)
        self.page.mouse.down()
        self.page.mouse.move(x0 + 40, y0 + 22.5, steps=4)
        self.page.mouse.up()
        expect(self.page.locator('#chooseRegion')).to_have_attribute('aria-pressed', 'false')
        expect(self.page.locator('#clearRegion')).to_be_enabled()
        expect(self.page.locator('#clearTarget')).to_be_disabled()
        self.start_scan()
        region = self.page.evaluate('() => window.__pendingPoseScans[0].region')
        for key, expected in {'x': .1, 'y': .1, 'width': .7, 'height': .7}.items():
            self.assertAlmostEqual(region[key], expected, delta=.006)
        self.finish_scan()
        report = self.download_report('forehand-drag-region.json')
        self.assertIsNone(report['options']['target'])

    def test_invalid_region_and_cancelled_selection_preserve_completed_result(self):
        self.use_fake_rules()
        self.load()
        self.start_scan()
        self.finish_scan()
        expect(self.page.locator('#exportReport')).to_be_enabled()
        self.set_region(80, 0, 50, 100)
        expect(self.page.locator('#regionStatus')).to_contain_text('画面内')
        expect(self.page.locator('#exportReport')).to_be_enabled()
        self.set_region(0, 0, 1, 100)
        expect(self.page.locator('#regionStatus')).to_contain_text('5%')
        expect(self.page.locator('#exportReport')).to_be_enabled()
        self.page.locator('#chooseRegion').click()
        self.page.locator('#previewCanvas').press('Escape')
        expect(self.page.locator('#chooseRegion')).to_have_attribute('aria-pressed', 'false')
        expect(self.page.locator('#exportReport')).to_be_enabled()
        report = self.download_report('forehand-invalid-region.json')
        self.assertIsNone(report['options']['region'])
        self.assertEqual(self.page.evaluate('() => window.__pendingPoseScans.length'), 1)

    def test_candidate_and_missing_interval_seeks_keep_half_speed(self):
        self.use_fake_rules()
        self.load()
        self.start_scan()
        self.finish_scan()
        expect(self.page.locator("#candidateList .candidate")).to_have_count(1)
        self.page.locator("#playbackRate").select_option("0.5")
        self.page.locator("#candidateList .candidate-top button").click()
        self.page.wait_for_function("() => !document.getElementById('sourceVideo').seeking")
        candidate_time = self.page.locator("#sourceVideo").evaluate("video => video.currentTime")
        self.assertAlmostEqual(candidate_time, .75, delta=.03)
        self.assertEqual(self.page.locator("#sourceVideo").evaluate("video => video.playbackRate"), .5)
        expect(self.page.locator("#reviewList button")).to_have_count(1)
        self.page.locator("#reviewDetails summary").click()
        self.page.locator("#reviewList button").click()
        self.page.wait_for_function("() => !document.getElementById('sourceVideo').seeking")
        review_time = self.page.locator("#sourceVideo").evaluate("video => video.currentTime")
        self.assertAlmostEqual(review_time, 1.4, delta=.03)
        self.assertEqual(self.page.locator("#sourceVideo").evaluate("video => video.playbackRate"), .5)

    def test_real_rules_do_not_treat_zero_or_missing_poses_as_good(self):
        self.load()
        self.page.locator("#handedness").select_option("right")
        self.page.locator("#practiceContext").select_option("forehand")
        for count, frames in enumerate(([], [{"time": n / 12, "poses": []} for n in range(25)]), 1):
            with self.subTest(sampled_frames=len(frames)):
                self.start_scan(count)
                self.finish_scan(count - 1, frames)
                expect(self.page.locator("#exportReport")).to_be_enabled()
                expect(self.page.locator("#candidateList button")).to_have_count(0)
                report = self.download_report(f"forehand-missing-poses-{count}.json")
                self.assertEqual(report["result"]["status"], "insufficient")
                self.assertEqual(report["result"]["summary"]["usableFrames"], 0)
                self.assertEqual(report["result"]["summary"]["coverage"], 0)
                text = self.page.locator("#resultsStatus").inner_text()
                self.assertNotIn("动作合格", text)
                self.assertNotIn("动作良好", text)

    def test_recorder_webm_discovers_duration_returns_to_start_and_can_scan(self):
        # Record a real synthetic canvas through the browser's MediaRecorder,
        # the same container path as the privacy editor's silent WebM output.
        encoded = self.page.evaluate("""async () => {
            const canvas = document.createElement('canvas'); canvas.width=64; canvas.height=64;
            const context = canvas.getContext('2d');
            const stream = canvas.captureStream(10);
            const recorder = new MediaRecorder(stream, {mimeType:'video/webm;codecs=vp8'});
            const parts=[];
            recorder.addEventListener('dataavailable', event => parts.push(event.data));
            const stopped = new Promise(resolve => recorder.addEventListener('stop', resolve, {once:true}));
            recorder.start(100);
            for (let i=0; i<6; i++) {
                context.fillStyle = i%2 ? '#38a86b' : '#d3c752'; context.fillRect(0,0,64,64);
                await new Promise(resolve => setTimeout(resolve, 100));
            }
            recorder.stop(); await stopped;
            stream.getTracks().forEach(track => track.stop());
            const bytes = new Uint8Array(await new Blob(parts, {type:'video/webm'}).arrayBuffer());
            return btoa(Array.from(bytes, byte => String.fromCharCode(byte)).join(''));
        }""")
        fixture = browser_helpers.OUTPUT / "synthetic-forehand-recorder.webm"
        fixture.write_bytes(base64.b64decode(encoded))
        self.page.evaluate("""() => {
            window.__metadataDurations=[];
            document.addEventListener('loadedmetadata', event => {
                if (event.target.id === 'sourceVideo') window.__metadataDurations.push(String(event.target.duration));
            }, true);
        }""")
        self.load(fixture)
        expect(self.page.locator("#analyzeButton")).to_be_enabled()
        self.assertEqual(self.page.evaluate("() => window.__metadataDurations[0]"), "Infinity")
        values = self.page.locator("#sourceVideo").evaluate("v => ({duration:v.duration,time:v.currentTime,seeking:v.seeking})")
        self.assertGreater(values["duration"], .2)
        self.assertLess(values["duration"], 2)
        self.assertLess(values["time"], .05)
        self.assertFalse(values["seeking"])
        self.start_scan()
        self.assertEqual(self.page.evaluate("() => window.__pendingPoseScans[0].fileName"), fixture.name)

    def test_duration_probe_keeps_overlong_streaming_webm_blocked(self):
        self.load(self.long_streaming_fixture)
        expect(self.page.locator("#analyzeButton")).to_be_disabled()
        expect(self.page.locator("#analysisStatus")).to_contain_text("超过 60 秒")
        values = self.page.locator("#sourceVideo").evaluate("v => ({duration:v.duration,time:v.currentTime,seeking:v.seeking})")
        self.assertGreater(values["duration"], 60)
        self.assertLess(values["time"], .05)
        self.assertFalse(values["seeking"])
        self.assertIsNone(self.page.evaluate("() => window.__pendingPoseScans"))

    def test_switching_file_during_duration_probe_discards_old_events(self):
        # Hold the first file's end seek open to exercise the load race without
        # relying on disk/decoder timing. The second file uses native metadata.
        self.page.evaluate("""() => {
            const video = document.getElementById('sourceVideo');
            const duration = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'duration');
            const time = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'currentTime');
            window.__heldSource=null; window.__heldProbe=false;
            document.addEventListener('loadedmetadata', event => {
                if (event.target === video && window.__heldSource===null) window.__heldSource=video.currentSrc;
            }, true);
            Object.defineProperty(video, 'duration', {configurable:true, get() {
                return this.currentSrc===window.__heldSource ? Infinity : duration.get.call(this);
            }});
            Object.defineProperty(video, 'currentTime', {configurable:true,
                get() { return time.get.call(this); },
                set(value) {
                    if (this.currentSrc===window.__heldSource && value > 1e8) {window.__heldProbe=true; return;}
                    time.set.call(this, value);
                },
            });
        }""")
        self.page.locator("#videoFile").set_input_files(str(self.fixture))
        self.page.wait_for_function("() => window.__heldProbe")
        expect(self.page.locator("#analyzeButton")).to_be_disabled()
        self.load(self.second_fixture)
        self.page.locator("#sourceVideo").evaluate("v => {v.dispatchEvent(new Event('durationchange')); v.dispatchEvent(new Event('seeked'));}")
        self.page.wait_for_timeout(100)
        expect(self.page.locator("#analyzeButton")).to_be_enabled()
        expect(self.page.locator("#fileName")).to_have_text(self.second_fixture.name)
        expect(self.page.locator("#analysisStatus")).to_contain_text("可以开始分析")
        self.assertLess(self.page.locator("#sourceVideo").evaluate("v => v.currentTime"), .05)
        self.start_scan()
        self.assertEqual(self.page.evaluate("() => window.__pendingPoseScans[0].fileName"), self.second_fixture.name)


if __name__ == "__main__":
    import unittest
    unittest.main()
