"""Check crop coordinate provenance with no downloaded models or personal media."""

import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PoseRegionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        local_deno = ROOT / '.venv' / 'Scripts' / 'deno.exe'
        cls.deno = str(local_deno) if local_deno.is_file() else shutil.which('deno')
        if not cls.deno:
            raise unittest.SkipTest('Install requirements-data.txt for the local Deno runtime')

    def evaluate(self, expression):
        script = f"import {{normalizeRegion, cropRect, mapLandmark}} from {json.dumps((ROOT / 'web' / 'pose_region.js').as_uri())};\nconsole.log(JSON.stringify({expression}));"
        result = subprocess.run([self.deno, 'eval', '--no-config', script], cwd=ROOT,
                                check=True, capture_output=True, text=True, encoding='utf-8', timeout=30)
        return json.loads(result.stdout)

    def test_fractional_crop_edges_round_outward_and_landmarks_return_to_original_video(self):
        result = self.evaluate("""(() => {
          const region=normalizeRegion({x:.101,y:.203,width:.604,height:.501});
          const crop=cropRect(region,641,359);
          const point={x:.25,y:.75,z:-.3,visibility:.7,presence:null};
          return {crop, mapped:mapLandmark(point,crop,641,359), original:point};
        })()""")
        crop = result['crop']
        self.assertEqual(crop, {'sx': 64, 'sy': 72, 'sw': 388, 'sh': 181})
        point = result['mapped']
        self.assertAlmostEqual(point['x'], (64 + .25 * 388) / 641)
        self.assertAlmostEqual(point['y'], (72 + .75 * 181) / 359)
        self.assertAlmostEqual(point['z'], -.3 * 388 / 641)
        self.assertEqual(point['visibility'], .7)
        self.assertIsNone(point['presence'])
        self.assertEqual(result['original']['x'], .25)

    def test_full_frame_identity_and_outside_landmarks_remain_outside(self):
        result = self.evaluate("""(() => {
          const crop=cropRect(null,640,360);
          const point={x:-.1,y:1.2,z:.4,visibility:.1};
          return {region:normalizeRegion(null),crop,point:mapLandmark(point,crop,640,360)};
        })()""")
        self.assertIsNone(result['region'])
        self.assertEqual(result['crop'], {'sx': 0, 'sy': 0, 'sw': 640, 'sh': 360})
        self.assertEqual(result['point'], {'x': -.1, 'y': 1.2, 'z': .4, 'visibility': .1})

    def test_invalid_regions_fail_instead_of_silently_analysing_another_area(self):
        result = self.evaluate("""(() => {
          const cases=[{x:.8,y:0,width:.5,height:1},{x:0,y:0,width:0,height:1},
            {x:0,y:0,width:.01,height:1},{x:'0',y:0,width:1,height:1},
            {x:NaN,y:0,width:1,height:1},{x:-.1,y:0,width:1,height:1}];
          return cases.map(region=>{try {normalizeRegion(region); return false;} catch{return true;}});
        })()""")
        self.assertEqual(result, [True] * 6)
        self.assertTrue(self.evaluate("""(() => {try {
          cropRect({x:0,y:0,width:.05,height:.05},100,100); return false;
        } catch {return true;}})()"""))


if __name__ == '__main__':
    unittest.main()
