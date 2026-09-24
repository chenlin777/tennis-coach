"""Tracking math and UI lifecycle tests with synthetic data.

The fake analysis module deliberately does not run machine learning. These
tests verify UI isolation, review requirements, and rendering/export only.
"""

import subprocess
import unittest

import test_privacy_browser as browser_helpers
from playwright.sync_api import expect


FAKE_ANALYSIS_MODULE = r"""
export const MODEL_VERSION = 'synthetic-ui-test-not-a-model';
export function disposeModels() {}
export function getMasksAt(analysis, time) {
  if (!analysis || time < 0 || time > 2 || (time >= .6 && time <= .9)) return [];
  return [{ x: .12 + .23 * time, y: .26, w: .16, h: .2, source: 'synthetic' }];
}
export function analyzeVideo(file, { signal, onProgress }) {
  onProgress({phase:'scanning', fraction:.25, time:.5, duration:2});
  return new Promise(resolve => {
    window.__pendingScans ??= [];
    window.__pendingScans.push({ resolve, onProgress, signal, fileName:file.name });
    // Ignore abort on purpose: the UI must reject late callbacks/results.
  });
}
"""


class AutoPrivacyTests(browser_helpers.BrowserTestCase):
    def use_fake_analysis(self):
        self.page.route("**/auto_privacy.js", lambda route: route.fulfill(
            status=200, content_type="text/javascript; charset=utf-8", body=FAKE_ANALYSIS_MODULE))

    def finish_scan(self, index=0, zero=False):
        self.page.evaluate("""({index, zero}) => {
            const scan = window.__pendingScans[index];
            scan.onProgress({phase:'scanning', fraction:1, time:2, duration:2});
            scan.resolve({
                frames:[{time:0, boxes: zero ? [] : [{x:.12,y:.26,w:.16,h:.2}]}],
                tracks:[], duration:2, detectedFrames:zero ? 0 : 17, totalFrames:21,
                reviewIntervals:zero ? [] : [{start:.6,end:.9,reason:'Synthetic missing detection interval'}],
                modelVersion:'synthetic-ui-test-not-a-model',
            });
        }""", {"index": index, "zero": zero})

    def wait_for_scans(self, count):
        self.page.wait_for_function("count => window.__pendingScans?.length === count", arg=count)

    def test_moving_masks_short_gaps_long_gaps_and_boundaries(self):
        result = self.page.evaluate("""async () => {
            const m = await import('/auto_privacy.js');
            const box = x => ({x,y:.2,w:.1,h:.15});
            const short = m.buildTimeline([
                {time:0,boxes:[box(.1)]}, {time:.1,boxes:[]}, {time:.2,boxes:[box(.15)]},
            ], .3);
            const long = m.buildTimeline([
                {time:0,boxes:[box(.1)]}, {time:.1,boxes:[]}, {time:.4,boxes:[]},
                {time:.7,boxes:[]}, {time:1,boxes:[box(.2)]},
            ], 1);
            return { short, long, between:m.getMasksAt(short,.1), gap:m.getMasksAt(long,.5),
                first:m.getMasksAt(short,0), last:m.getMasksAt(long,1),
                negative:m.getMasksAt(short,-.001), beyond:m.getMasksAt(short,1),
                nan:m.getMasksAt(short,NaN), infinite:m.getMasksAt(short,Infinity) };
        }""")
        self.assertEqual(len(result["short"]["tracks"]), 1)
        self.assertEqual(len(result["between"]), 1)
        self.assertLessEqual(result["between"][0]["x"], .1)
        self.assertGreaterEqual(result["between"][0]["x"] + result["between"][0]["w"], .25)
        self.assertEqual(len(result["long"]["tracks"]), 2)
        self.assertEqual(result["gap"], [], "Long missing intervals must not be filled with guessed positions")
        self.assertTrue(any(interval["start"] <= .5 <= interval["end"] for interval in result["long"]["reviewIntervals"]))
        self.assertEqual(len(result["first"]), 1)
        self.assertEqual(len(result["last"]), 1)
        for key in ("negative", "beyond", "nan", "infinite"):
            self.assertEqual(result[key], [], key)

    def test_crossing_targets_do_not_share_one_track_at_the_same_time(self):
        result = self.page.evaluate("""async () => {
            const m = await import('/auto_privacy.js');
            const box = x => ({x,y:.3,w:.08,h:.13});
            const frames = [
                {time:0,boxes:[box(.15),box(.55)]},
                {time:.1,boxes:[box(.25),box(.45)]},
                {time:.2,boxes:[box(.36),box(.34)]},
                {time:.3,boxes:[box(.46),box(.24)]},
                {time:.4,boxes:[box(.56),box(.14)]},
            ];
            const timeline=m.buildTimeline(frames,.4);
            return {timeline, masks:frames.map(frame=>({time:frame.time, boxes:frame.boxes,
                masks:m.getMasksAt(timeline,frame.time)}))};
        }""")
        tracks = result["timeline"]["tracks"]
        self.assertEqual(len(tracks), 2)
        for track in tracks:
            times = [point["time"] for point in track["points"]]
            self.assertEqual(len(times), len(set(times)), "Two detections cannot share one track at the same frame")
        # This is a privacy coverage check, not a claim of identity recognition.
        for frame in result["masks"]:
            for box in frame["boxes"]:
                x, y = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
                self.assertTrue(any(mask["x"] <= x <= mask["x"] + mask["w"] and
                                    mask["y"] <= y <= mask["y"] + mask["h"] for mask in frame["masks"]))

    def test_pose_head_requires_reliable_points_and_clips_to_the_frame(self):
        result = self.page.evaluate("""async () => {
            const m=await import('/auto_privacy.js');
            const landmarks=Array.from({length:33},()=>({x:.02,y:.05,visibility:1}));
            landmarks[11]={x:.01,y:.2,visibility:1}; landmarks[12]={x:.2,y:.2,visibility:1};
            landmarks[23]={x:.02,y:.4,visibility:1}; landmarks[24]={x:.2,y:.4,visibility:1};
            const clipped=m.headBoxFromPose(landmarks,640,360);
            const hidden=landmarks.map(p=>({...p,visibility:.1}));
            const badShoulders=landmarks.map(p=>({...p}));badShoulders[11].visibility=.1;
            const sparse=landmarks.map(p=>({...p}));for(let i=2;i<11;i++)sparse[i].x=NaN;
            return {clipped, hidden:m.headBoxFromPose(hidden,640,360),
                badShoulders:m.headBoxFromPose(badShoulders,640,360),
                sparse:m.headBoxFromPose(sparse,640,360)};
        }""")
        for key in ("hidden", "badShoulders", "sparse"):
            self.assertIsNone(result[key], key)
        box = result["clipped"]
        self.assertIsNotNone(box)
        self.assertEqual(box["x"], 0)
        self.assertEqual(box["y"], 0)
        self.assertGreater(box["w"], 0)
        self.assertGreater(box["h"], 0)
        self.assertLessEqual(box["x"] + box["w"], 1)
        self.assertLessEqual(box["y"] + box["h"], 1)

    def test_scan_cancel_file_switch_and_late_results_are_isolated(self):
        self.use_fake_analysis()
        self.load()
        expect(self.page.locator('input[name="faceMode"][value="auto"]')).to_be_checked()
        expect(self.page.locator("#exportVideo")).to_be_disabled()
        self.page.locator("#scanVideo").click()
        self.wait_for_scans(1)
        expect(self.page.locator("#analysisProgress")).to_have_attribute("value", "25")
        expect(self.page.locator("#videoFile")).to_be_enabled()
        expect(self.page.locator("#playPause")).to_be_disabled()
        expect(self.page.locator("#exportVideo")).to_be_disabled()
        self.page.locator("#cancelScan").click()
        expect(self.page.locator("#analysisStatus")).to_contain_text("已取消")
        self.assertTrue(self.page.evaluate("() => window.__pendingScans[0].signal.aborted"))
        self.finish_scan(0)
        expect(self.page.locator("#analysisSummary")).to_be_hidden()
        expect(self.page.locator("#exportVideo")).to_be_disabled()

        self.page.locator("#scanVideo").click()
        self.wait_for_scans(2)
        self.load(self.second_fixture)
        self.assertTrue(self.page.evaluate("() => window.__pendingScans[1].signal.aborted"))
        progress_before_late_result = self.page.locator("#analysisProgress").evaluate("el => el.value")
        self.finish_scan(1)
        expect(self.page.locator("#fileName")).to_have_text(self.second_fixture.name)
        expect(self.page.locator("#analysisSummary")).to_be_hidden()
        expect(self.page.locator("#reviewPanel")).to_be_hidden()
        expect(self.page.locator("#exportVideo")).to_be_disabled()
        expect(self.page.locator("#analysisProgress")).to_be_hidden()
        self.assertEqual(self.page.locator("#analysisProgress").evaluate("el => el.value"), progress_before_late_result)

    def test_zero_detection_never_enables_auto_export(self):
        self.use_fake_analysis()
        self.load()
        self.page.locator("#scanVideo").click()
        self.wait_for_scans(1)
        self.finish_scan(zero=True)
        expect(self.page.locator("#analysisStatus")).to_contain_text("未检测到")
        expect(self.page.locator("#analysisSummary")).to_be_hidden()
        expect(self.page.locator("#exportVideo")).to_be_disabled()
        expect(self.page.locator("#scanVideo")).to_be_enabled()

    def test_gap_review_then_dynamic_masks_survive_export(self):
        self.use_fake_analysis()
        self.load()
        self.page.locator("#scanVideo").click()
        self.wait_for_scans(1)
        self.finish_scan()
        expect(self.page.locator("#analysisSummary")).to_be_visible()
        expect(self.page.locator("#reviewPanel")).to_be_visible()
        expect(self.page.locator("#reviewList button")).to_have_count(1)
        expect(self.page.locator("#exportVideo")).to_be_disabled()
        self.page.locator("#reviewList button").click()
        self.page.wait_for_function("() => Math.abs(document.getElementById('sourceVideo').currentTime - .6) < .02")
        self.page.locator("#reviewConfirm").check()
        expect(self.page.locator("#exportVideo")).to_be_enabled()
        output = self.download_export("synthetic-auto-dynamic.webm")
        decoded = subprocess.run([
            self.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(output),
            "-vf", "fps=10", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ], check=True, capture_output=True, timeout=30).stdout
        frame_size = browser_helpers.WIDTH * browser_helpers.HEIGHT * 3
        count = len(decoded) // frame_size
        self.assertGreaterEqual(count, 15)
        self.assertLessEqual(count, 30)
        for frame_index in (0, count // 2, count - 1):
            with self.subTest(dynamic_export_frame=frame_index):
                time = min(1.95, frame_index / 10)
                center_x = round((.12 + .23 * time + .08) * browser_helpers.WIDTH)
                frame = memoryview(decoded)[frame_index * frame_size:(frame_index + 1) * frame_size]
                for dx in (-4, 0, 4):
                    for y in (111, 124, 139):
                        offset = (y * browser_helpers.WIDTH + center_x + dx) * 3
                        actual = frame[offset:offset + 3]
                        self.assertLessEqual(max(abs(actual[c] - browser_helpers.MASK_RGB[c]) for c in range(3)), 12,
                                             f"Dynamic mask missing at frame {frame_index}, x={center_x}, rgb={list(actual)}")
        # A fixed mask at the initial location would fail this separate check.
        last = memoryview(decoded)[(count - 1) * frame_size:count * frame_size]
        old_center = (125 * browser_helpers.WIDTH + round(.2 * browser_helpers.WIDTH)) * 3
        self.assertGreater(max(abs(last[old_center + c] - browser_helpers.MASK_RGB[c]) for c in range(3)), 20)


if __name__ == "__main__":
    unittest.main()
