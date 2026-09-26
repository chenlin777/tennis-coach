"""Synthetic invariants for 2D candidate screening, not coaching accuracy tests.

The geometry is deliberately invented; real-video validation is separate.
Runs ES modules through local Deno without any network access.
"""

import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
DENO = ROOT / '.venv' / 'Scripts' / 'deno.exe'

JS_FIXTURES = r"""
const options = {width:1000,height:1000,duration:2,handedness:'right',context:'forehand'};
function pose(offHeight=.9, hitX=.85, hitHeight=.1, shift=0) {
  const p=Array.from({length:33},()=>({x:.5+shift,y:.5,visibility:.99,presence:.99}));
  for (const [index,x,y] of [[11,.42,.35],[12,.58,.35],[13,.35,.43],[14,.71,.48],[23,.44,.6],[24,.56,.6]])
    p[index]={x:x+shift,y,visibility:.99,presence:.99};
  p[15]={x:.32+shift,y:.6-offHeight*.25,visibility:.99,presence:.99};
  p[16]={x:hitX+shift,y:.6-hitHeight*.25,visibility:.99,presence:.99};
  return p;
}
function sequence(kind='drop') {
  return Array.from({length:21},(_,i)=>{
    const t=i/10, progress=Math.max(0,Math.min(1,(t-.6)/.6));
    const off=kind==='raised' ? .9 : kind==='low' ? .15 : t<.5 ? .9 : .15;
    const hitX=.85-.43*progress, hitHeight=.1+progress;
    return {time:t,poses:[pose(off,hitX,hitHeight)]};
  });
}
"""


class ArmRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deno = str(DENO) if DENO.is_file() else shutil.which('deno')
        if not cls.deno:
            raise unittest.SkipTest('Install requirements-data.txt for the local Deno runtime')

    def evaluate(self, expression):
        script = f"import {{analyzeArm, ARM_RULES_VERSION}} from {json.dumps((ROOT / 'web' / 'arm_rules.js').as_uri())};\n{JS_FIXTURES}\nconst result=({expression});\nconsole.log(JSON.stringify(result));"
        result = subprocess.run([self.deno, 'eval', '--no-config', script], cwd=ROOT,
                                check=True, capture_output=True, text=True, encoding='utf-8', timeout=30)
        return json.loads(result.stdout)

    def test_drop_is_tentative_with_temporal_evidence_and_raised_hand_is_not(self):
        results = self.evaluate("[analyzeArm(sequence(),options), analyzeArm(sequence('raised'),options), analyzeArm(sequence('low'),options)]")
        drop, raised, low = results
        self.assertEqual(drop['status'], 'candidates')
        self.assertEqual(len(drop['candidates']), 1)
        candidate = drop['candidates'][0]
        self.assertEqual(candidate['confidence'], 'tentative')
        self.assertEqual(candidate['evidence']['kind'], 'lowering_during_swing')
        self.assertLess(candidate['evidence']['raisedTime'], candidate['time'])
        self.assertGreater(candidate['evidence']['swingEnd'], candidate['time'])
        self.assertEqual(raised['candidates'], [])
        self.assertEqual(low['candidates'][0]['evidence']['kind'], 'low_during_swing')

    def test_missing_wrists_presence_and_out_of_frame_never_mean_lowering(self):
        results = self.evaluate("""(() => ['visibility','presence','bounds','missing'].map(kind => {
          const frames=sequence();
          for(const frame of frames) if(frame.time>=.4 && frame.time<=1.3) {
            if(kind==='bounds') frame.poses[0][15].x=1.2;
            else if(kind==='missing') frame.poses[0][15]=null;
            else frame.poses[0][15][kind]=.1;
          }
          return analyzeArm(frames,options);
        }))()""")
        for result in results:
            self.assertEqual(result['candidates'], [])
            self.assertTrue(result['reviewIntervals'])
            self.assertLess(result['summary']['coverage'], 1)

    def test_context_gates_preserve_tracking_without_claiming_a_problem(self):
        results = self.evaluate("""[analyzeArm(sequence(),{...options,handedness:'unknown'}),
          ...['unknown','mixed','self_feed'].map(context=>analyzeArm(sequence(),{...options,context}))]""")
        for result in results:
            self.assertEqual(result['status'], 'context_required')
            self.assertEqual(result['candidates'], [])
            self.assertEqual(result['summary']['coverage'], 1)

    def test_absent_optional_presence_preserves_visibility_checks(self):
        results = self.evaluate("""(() => ['omitted','null'].map(kind => {
          const frames=sequence();for(const frame of frames)for(const point of frame.poses[0]) {
            if(kind==='null')point.presence=null;else delete point.presence;
          }
          return analyzeArm(frames,options);
        }))()""")
        for result in results:
            self.assertEqual(result['status'], 'candidates')
            self.assertEqual(result['summary']['coverage'], 1)

    def test_hidden_elbows_do_not_discard_reliable_wrist_evidence(self):
        results = self.evaluate("""(() => ['visibility','presence','missing'].map(kind => {
          const frames=sequence();for(const frame of frames)for(const index of [13,14]) {
            if(kind==='missing')frame.poses[0][index]=null;
            else frame.poses[0][index][kind]=.1;
          }
          return analyzeArm(frames,options);
        }))()""")
        for result in results:
            self.assertEqual(result['status'], 'candidates')
            self.assertEqual(result['summary']['coverage'], 1)
            self.assertEqual(result['candidates'][0]['evidence']['kind'], 'lowering_during_swing')

    def test_explicit_target_resolves_people_and_identity_does_not_restart(self):
        results = self.evaluate("""(() => {
          const frames=sequence();for(const frame of frames)frame.poses.push(pose(.9,.65,.1,-.3));
          const ambiguous=analyzeArm(frames,options);
          const selected=analyzeArm(frames,{...options,target:{x:.5,y:.48},targetTime:0});
          const gap=sequence();for(const frame of gap)if(frame.time>=.4 && frame.time<=1.1)frame.poses=[];
          const lost=analyzeArm(gap,options);
          return {ambiguous,selected,lost};
        })()""")
        self.assertEqual(results['ambiguous']['status'], 'insufficient')
        self.assertEqual(results['ambiguous']['summary']['usableFrames'], 0)
        self.assertEqual(results['selected']['status'], 'candidates')
        self.assertEqual(results['lost']['candidates'], [])
        self.assertIsNone(results['lost']['trackedFrames'][-1]['landmarks'])

    def test_mirror_and_left_hand_swap_anatomy_once(self):
        results = self.evaluate("""(() => {
          const frames=sequence();
          for(const frame of frames)for(const [a,b] of [[11,12],[13,14],[15,16],[23,24]])
            [frame.poses[0][a],frame.poses[0][b]]=[frame.poses[0][b],frame.poses[0][a]];
          return [analyzeArm(frames,{...options,mirrored:true}),analyzeArm(frames,{...options,handedness:'left'})];
        })()""")
        for result in results:
            self.assertEqual(result['status'], 'candidates')
            self.assertEqual(result['trackedFrames'][0]['features']['offWristIndex'], 16)

    def test_camera_aspect_ratio_does_not_change_same_pixel_geometry(self):
        results = self.evaluate("""(() => {
          const original=sequence(), wide=sequence();
          for(const frame of wide)for(const p of frame.poses[0])p.x/=2;
          return [analyzeArm(original,options),analyzeArm(wide,{...options,width:2000})];
        })()""")
        self.assertEqual(results[0]['candidates'], results[1]['candidates'])

    def test_walking_and_post_finish_relaxation_are_not_early_drops(self):
        results = self.evaluate("""(() => {
          const walking=Array.from({length:21},(_,i)=>({time:i/10,poses:[pose(.1,.72,.1,i*.004)]}));
          const walkingArms=Array.from({length:21},(_,i)=>({time:i/10,poses:[pose(.1,.6+.15*Math.sin(i/3),.25+.2*Math.cos(i/3),i*.004)]}));
          const recovery=Array.from({length:21},(_,i)=>({time:i/10,poses:[pose(i<8?.9:.1,.43,i<8?1.0:.7)]}));
          return [analyzeArm(walking,options),analyzeArm(walkingArms,options),analyzeArm(recovery,options)];
        })()""")
        for result in results:
            self.assertEqual(result['candidates'], [])

    def test_sparse_samples_and_one_frame_jitter_are_not_evidence(self):
        results = self.evaluate("""(() => {
          const sparse=sequence().filter((_,i)=>i%4===0);
          const jitter=sequence('raised');jitter[5].poses[0][15].y=.6;
          return [analyzeArm(sparse,options),analyzeArm(jitter,options)];
        })()""")
        for result in results:
            self.assertEqual(result['candidates'], [])
        self.assertTrue(results[0]['reviewIntervals'])


if __name__ == '__main__':
    unittest.main()
