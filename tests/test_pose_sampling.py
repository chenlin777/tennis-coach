"""Real video decoding/cropping with a controlled, local pose detector.

These check coordinate provenance and model/resource isolation, not pose quality.
Only the MediaPipe module is replaced; sampling and video decoding remain real.
"""

import test_privacy_browser as browser_helpers


FAKE_MEDIAPIPE = r"""
window.__poseHarness = {models:[], events:[], detections:[], closedResults:0};
const log=window.__poseHarness;
export class FilesetResolver {
  static async forVisionTasks(path) { log.filesPath=path; return {}; }
}
export class PoseLandmarker {
  static async createFromOptions(files, options) {
    const record={id:log.models.length, path:options.baseOptions.modelAssetPath, closed:false};
    log.models.push(record); log.events.push({kind:'create',id:record.id});
    return {
      async setOptions(options) {
        if(record.closed) throw Error('reset after close');
        log.events.push({kind:'mode',id:record.id,mode:options.runningMode});
      },
      detectForVideo(canvas, timestamp) {
        if(record.closed) throw Error('detect after close');
        log.detections.push({id:record.id,width:canvas.width,height:canvas.height,timestamp,
          pixel:[...canvas.getContext('2d').getImageData(0,0,1,1).data]});
        return {landmarks:[[
          {x:.25,y:.75,z:-.3,visibility:.8,presence:.9},
          {x:-.1,y:.5,z:.2,visibility:.98},
          {x:.5,y:1.1,z:.1,visibility:.99},
        ]],close(){log.closedResults++;}};
      },
      close() {
        if(record.closed) throw Error('model closed twice');
        record.closed=true; log.events.push({kind:'close',id:record.id});
      },
    };
  }
}
"""


class PoseSamplingTests(browser_helpers.BrowserTestCase):
    def setUp(self):
        super().setUp()
        self.page.route('**/vendor/mediapipe/vision_bundle.mjs', lambda route: route.fulfill(
            status=200, content_type='text/javascript; charset=utf-8', body=FAKE_MEDIAPIPE))
        self.page.goto(self.address + 'forehand.html')
        self.page.evaluate("""() => {
          const create=URL.createObjectURL.bind(URL), revoke=URL.revokeObjectURL.bind(URL);
          window.__samplingURLs=new Set();
          URL.createObjectURL=blob=>{const url=create(blob); window.__samplingURLs.add(url); return url;};
          URL.revokeObjectURL=url=>{window.__samplingURLs.delete(url); return revoke(url);};
        }""")
        self.select_file(self.fixture)

    def select_file(self, path):
        self.page.locator('#videoFile').set_input_files(str(path))
        self.page.wait_for_function("""() => {
          const video=document.getElementById('sourceVideo');
          return video.readyState>=2 && Number.isFinite(video.duration) && !video.seeking
            && !document.getElementById('analyzeButton').disabled;
        }""")

    def scan(self, variant='lite', region=None):
        return self.page.evaluate("""async ({variant,region}) => {
          const {scanPoses}=await import('./pose_analysis.js');
          return scanPoses(document.getElementById('videoFile').files[0],
            {modelVariant:variant,region,sampleFps:1});
        }""", {'variant': variant, 'region': region})

    def assert_sampler_resources_released(self):
        # Exactly one video and one object URL belong to the preview page.
        self.assertEqual(self.page.evaluate('() => document.querySelectorAll("video").length'), 1)
        self.assertEqual(self.page.evaluate('() => window.__samplingURLs.size'), 1)

    def test_crop_pixels_and_landmarks_retain_original_video_coordinates(self):
        region = {'x': .101, 'y': .203, 'width': .604, 'height': .501}
        expected_pixel = self.page.evaluate("""() => {
          const canvas=document.createElement('canvas'); canvas.width=640; canvas.height=360;
          const context=canvas.getContext('2d');
          context.drawImage(document.getElementById('sourceVideo'),0,0);
          return [...context.getImageData(64,73,1,1).data];
        }""")
        result = self.scan(region=region)
        crop = {'sx': 64, 'sy': 73, 'sw': 388, 'sh': 181}
        self.assertEqual(result['samplingRegion'], {
            'pixelRect': crop, 'sourceWidth': 640, 'sourceHeight': 360})
        self.assertEqual((result['width'], result['height']), (640, 360))
        self.assertEqual((result['sampledWidth'], result['sampledHeight']), (388, 181))
        detections = self.page.evaluate('() => window.__poseHarness.detections')
        self.assertGreaterEqual(len(detections), 2)
        self.assertTrue(all((d['width'], d['height']) == (388, 181) for d in detections))
        self.assertEqual(detections[0]['pixel'], expected_pixel)
        point, left_outside, bottom_outside = result['frames'][0]['poses'][0]
        self.assertAlmostEqual(point['x'], (64 + .25 * 388) / 640)
        self.assertAlmostEqual(point['y'], (73 + .75 * 181) / 360)
        self.assertAlmostEqual(point['z'], -.3 * 388 / 640)
        self.assertEqual((point['visibility'], point['presence']), (.8, .9))
        for point in (left_outside, bottom_outside):
            self.assertGreater(point['x'], 0)
            self.assertLess(point['x'], 1)
            self.assertGreater(point['y'], 0)
            self.assertLess(point['y'], 1)
            self.assertEqual(point['visibility'], 0, 'Crop-exterior prediction cannot become visible in full image')
            self.assertIsNone(point['presence'])
        self.assertEqual(self.page.evaluate('() => window.__poseHarness.closedResults'), len(detections))
        self.assert_sampler_resources_released()

    def test_region_file_and_model_changes_reset_tracking_and_close_replaced_model(self):
        first = self.scan('lite', {'x': .1, 'y': .1, 'width': .7, 'height': .8})
        second = self.scan('lite', {'x': .2, 'y': .2, 'width': .6, 'height': .6})
        self.select_file(self.second_fixture)
        third = self.scan('lite')
        fourth = self.scan('full', {'x': .2, 'y': .1, 'width': .5, 'height': .8})
        fifth = self.scan('lite')
        self.assertEqual(first['modelVersion'], second['modelVersion'])
        self.assertEqual(third['modelVersion'], 'mediapipe-1.0.1/pose-lite-v1')
        self.assertEqual(fourth['modelVersion'], 'mediapipe-1.0.1/pose-full-v1')
        self.assertEqual(fifth['modelVersion'], first['modelVersion'])
        self.assertNotEqual(first['samplingRegion'], second['samplingRegion'])
        self.assertIsNone(third['samplingRegion'])
        self.assertIsNone(fifth['samplingRegion'])
        log = self.page.evaluate('() => window.__poseHarness')
        self.assertEqual([m['path'].split('/')[-1] for m in log['models']], [
            'pose_landmarker_lite.task', 'pose_landmarker_full.task', 'pose_landmarker_lite.task'])
        self.assertEqual([m['closed'] for m in log['models']], [True, True, False])
        modes = [(e['id'], e['mode']) for e in log['events'] if e['kind'] == 'mode']
        self.assertEqual(modes, [(0, 'IMAGE'), (0, 'VIDEO')] * 3 +
                         [(1, 'IMAGE'), (1, 'VIDEO'), (2, 'IMAGE'), (2, 'VIDEO')])
        timestamps = [d['timestamp'] for d in log['detections']]
        self.assertTrue(all(a < b for a, b in zip(timestamps, timestamps[1:])))
        events = [(e['kind'], e['id']) for e in log['events'] if e['kind'] in ('create', 'close')]
        self.assertEqual(events, [('create', 0), ('close', 0), ('create', 1), ('close', 1), ('create', 2)])
        self.page.evaluate("async () => (await import('./pose_analysis.js')).disposePoseModel()")
        self.assertTrue(self.page.evaluate('() => window.__poseHarness.models.every(model=>model.closed)'))
        self.assert_sampler_resources_released()

    def test_invalid_options_decode_failure_and_abort_release_resources_and_queue(self):
        errors = self.page.evaluate("""async () => {
          const {scanPoses}=await import('./pose_analysis.js');
          const file=document.getElementById('videoFile').files[0];
          const errors=[];
          for(const options of [
            {modelVariant:'lite',region:{x:.9,y:0,width:.5,height:1}},
            {modelVariant:'unavailable'},
          ]) {
            try {await scanPoses(file,options); errors.push(null);}
            catch(error) {errors.push(error.message);}
          }
          try {await scanPoses(new Blob(['invalid video'],{type:'video/mp4'}),{modelVariant:'lite'}); errors.push(null);}
          catch(error) {errors.push(error.message);}
          return errors;
        }""")
        self.assertTrue(all(errors))
        self.assertIn('区域', errors[0])
        self.assertIn('模型', errors[1])
        self.assertIsNone(self.page.evaluate('() => window.__poseHarness'))
        self.assert_sampler_resources_released()
        aborted = self.page.evaluate("""async () => {
          const {scanPoses}=await import('./pose_analysis.js');
          const controller=new AbortController();
          try {
            await scanPoses(document.getElementById('videoFile').files[0],{
              modelVariant:'lite',sampleFps:1,signal:controller.signal,
              onProgress(progress){if(progress.phase==='scanning') controller.abort();},
            });
            return null;
          } catch(error) {return error.name;}
        }""")
        self.assertEqual(aborted, 'AbortError')
        self.assertEqual(self.page.evaluate('() => window.__poseHarness.detections.length'), 1)
        self.assert_sampler_resources_released()
        recovered = self.scan('lite')
        self.assertGreaterEqual(len(recovered['frames']), 2)
        self.assertEqual(self.page.evaluate('() => window.__poseHarness.models.length'), 1)
        self.assert_sampler_resources_released()


if __name__ == '__main__':
    import unittest
    unittest.main()
