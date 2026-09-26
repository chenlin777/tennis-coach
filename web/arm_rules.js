// Geometric screening only: these thresholds are engineering defaults, not
// coaching standards. No ball, racket, impact, gaze, or stroke classifier here.
export const ARM_RULES_VERSION = 'aux-arm-2d/0.1.1';
export const ARM_THRESHOLDS = Object.freeze({
  visibility: .55, presence: .55, minTorsoPixels: 18,
  maxGapSeconds: .26, maxIdentityGapSeconds: .55,
  lowWristHeight: .43, raisedWristHeight: .68, minimumDrop: .28,
  swingTravel: .75, swingRise: .28, swingEndHeight: .78, minimumSwingSpeed: 1.0,
  swingWindowSeconds: .65, beforeWindowSeconds: .6, eventSeparationSeconds: 1.0,
});

const finite = Number.isFinite;
const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
const middle = (a, b) => ({x: (a.x + b.x) / 2, y: (a.y + b.y) / 2});
const validPoint = p => p && finite(p.x) && finite(p.y) && p.x >= 0 && p.x <= 1 &&
  p.y >= 0 && p.y <= 1 && finite(p.visibility) && p.visibility >= ARM_THRESHOLDS.visibility &&
  (p.presence == null || (finite(p.presence) && p.presence >= ARM_THRESHOLDS.presence));
const px = (p, width, height) => ({x: p.x * width, y: p.y * height});

function body(pose, width, height) {
  if (!Array.isArray(pose) || pose.length < 33 || ![11, 12, 23, 24].every(i => validPoint(pose[i]))) return null;
  const shoulder = middle(px(pose[11], width, height), px(pose[12], width, height));
  const hip = middle(px(pose[23], width, height), px(pose[24], width, height));
  const scale = distance(shoulder, hip), center = middle(shoulder, hip);
  if (scale < ARM_THRESHOLDS.minTorsoPixels || shoulder.y >= hip.y - scale * .35) return null;
  return {pose, shoulder, hip, center, scale};
}

function chooseInitial(people, target, width, height) {
  if (!people.length) return {reason: '未看到可靠的肩部和髋部关键点'};
  if (target) {
    const point = px(target, width, height);
    const ranked = people.map(person => ({person, score: distance(point, person.center) / person.scale}))
      .sort((a, b) => a.score - b.score);
    if (ranked[0].score > 1.35) return {reason: '所选位置附近没有可靠的人体，请在躯干处重新选择'};
    if (ranked[1] && ranked[1].score - ranked[0].score < .22) return {reason: '所选位置对应多人重叠，暂不能确定分析对象'};
    return {person: ranked[0].person};
  }
  const ranked = [...people].sort((a, b) => b.scale - a.scale);
  if (ranked[1] && ranked[0].scale < ranked[1].scale * 1.5) {
    return {reason: '画面内有多个大小接近的人，请点击要分析者的躯干'};
  }
  return {person: ranked[0]};
}

function associate(people, previous, dt) {
  const ranked = people.map(person => {
    const ratio = person.scale / previous.scale;
    const motion = distance(person.center, previous.center) / previous.scale;
    const score = motion + Math.abs(Math.log(ratio));
    return {person, ratio, motion, score};
  }).filter(item => item.ratio >= .67 && item.ratio <= 1.5 && item.motion <= .42 + Math.min(dt, .3) * 1.5)
    .sort((a, b) => a.score - b.score);
  if (!ranked.length) return {reason: '人物关键点缺失、出画或镜头发生变化'};
  if (ranked[1] && ranked[1].score - ranked[0].score < .22) return {reason: '多人接近或交叉，无法连续确认同一人'};
  return {person: ranked[0].person};
}

function features(person, options) {
  const indices = options.handedness === 'left' ? {off: [12, 14, 16], hit: [11, 13, 15]} : {off: [11, 13, 15], hit: [12, 14, 16]};
  // Explicit mirror setting represents the anatomical index convention of the
  // mirrored input; x/y coordinates are already in displayed image space.
  if (options.mirrored) [indices.off, indices.hit] = [indices.hit, indices.off];
  // This rule consumes shoulders and wrists. Elbow occlusion must not discard
  // reliable wrist evidence; a future elbow-based rule needs its own gate.
  if (![indices.off[0], indices.off[2], indices.hit[0], indices.hit[2]].every(i => validPoint(person.pose[i]))) return null;
  const relative = index => {
    const p = px(person.pose[index], options.width, options.height);
    return {x: (p.x - person.hip.x) / person.scale, y: (p.y - person.hip.y) / person.scale};
  };
  const off = relative(indices.off[2]), hit = relative(indices.hit[2]);
  const offShoulder = relative(indices.off[0]), hitShoulder = relative(indices.hit[0]);
  return {
    offWristHeight: -off.y, hittingWristHeight: -hit.y,
    offWrist: off, hittingWrist: hit, offShoulder, hittingShoulder: hitShoulder,
    torsoPixels: person.scale, offWristIndex: indices.off[2], hittingWristIndex: indices.hit[2],
    finishLike: distance(hit, offShoulder) < .58 && -hit.y > .8,
  };
}

function intervalsFromFrames(tracked, duration) {
  const intervals = [];
  if (tracked.length && tracked[0].time > ARM_THRESHOLDS.maxGapSeconds) {
    intervals.push({start: 0, end: tracked[0].time, reason: '片段开始部分没有姿态采样'});
  }
  for (let i = 0; i < tracked.length; i++) {
    const frame = tracked[i];
    if (frame.status === 'usable') continue;
    const start = i === 0 ? 0 : (tracked[i - 1].time + frame.time) / 2;
    const end = i === tracked.length - 1 ? duration : (frame.time + tracked[i + 1].time) / 2;
    const previous = intervals.at(-1);
    if (previous && previous.reason === frame.reason && start <= previous.end + .01) previous.end = end;
    else intervals.push({start: Math.max(0, start), end: Math.max(start, end), reason: frame.reason});
  }
  // Sparse timestamps are missing evidence even if both endpoints look valid.
  for (let i = 1; i < tracked.length; i++) {
    if (tracked[i].time - tracked[i - 1].time > ARM_THRESHOLDS.maxGapSeconds) {
      intervals.push({start: tracked[i - 1].time, end: tracked[i].time, reason: '采样间隔过长，期间动作无法判断'});
    }
  }
  if (tracked.length && duration - tracked.at(-1).time > ARM_THRESHOLDS.maxGapSeconds) {
    intervals.push({start: tracked.at(-1).time, end: duration, reason: '片段结束部分没有姿态采样'});
  }
  return intervals.sort((a, b) => a.start - b.start);
}

function continuousWindow(frames, index, before, after) {
  let start = index, end = index;
  while (start > 0 && frames[index].time - frames[start - 1].time <= before &&
    frames[start - 1].status === 'usable' && frames[start].time - frames[start - 1].time <= ARM_THRESHOLDS.maxGapSeconds) start--;
  while (end + 1 < frames.length && frames[end + 1].time - frames[index].time <= after &&
    frames[end + 1].status === 'usable' && frames[end + 1].time - frames[end].time <= ARM_THRESHOLDS.maxGapSeconds) end++;
  return {before: frames.slice(start, index + 1), after: frames.slice(index, end + 1)};
}

function screenCandidates(tracked, options) {
  const candidates = [];
  for (let i = 0; i < tracked.length; i++) {
    const current = tracked[i], f = current.features;
    if (current.status !== 'usable' || f.offWristHeight > ARM_THRESHOLDS.lowWristHeight) continue;
    const {before, after} = continuousWindow(tracked, i, ARM_THRESHOLDS.beforeWindowSeconds, ARM_THRESHOLDS.swingWindowSeconds);
    if (before.length < 3 || after.length < 3 || current.time - before[0].time < .18) continue;
    // A lowered support hand after a high, across-body finish is ordinary
    // recovery. Do not call the recovery phase an early lowering.
    if (f.finishLike || before.slice(-4).some(frame => frame.features.finishLike)) continue;
    if (f.hittingWristHeight > 1.1) continue;
    const future = after.filter(frame => frame.time - current.time >= .18 &&
      frame.features.hittingWristHeight - f.hittingWristHeight >= ARM_THRESHOLDS.swingRise &&
      frame.features.hittingWristHeight >= ARM_THRESHOLDS.swingEndHeight);
    const swing = future.map(frame => ({frame, travel: distance(f.hittingWrist, frame.features.hittingWrist)}))
      .filter(item => item.travel >= ARM_THRESHOLDS.swingTravel && item.travel / (item.frame.time - current.time) >= ARM_THRESHOLDS.minimumSwingSpeed)
      .sort((a, b) => b.travel - a.travel)[0];
    if (!swing) continue;
    const raised = before.reduce((best, frame) => frame.features.offWristHeight > best.features.offWristHeight ? frame : best, before[0]);
    const drop = raised.features.offWristHeight - f.offWristHeight;
    const lowering = raised.features.offWristHeight >= ARM_THRESHOLDS.raisedWristHeight && drop >= ARM_THRESHOLDS.minimumDrop;
    const lowBefore = before.filter(frame => frame.features.offWristHeight <= ARM_THRESHOLDS.lowWristHeight);
    const persistentlyLow = lowBefore.length >= 3 && current.time - lowBefore[0].time >= .2 &&
      lowBefore.length / before.length >= .75;
    if (!lowering && !persistentlyLow) continue;
    // Require repeated low observations; a one-frame wrist swap/jitter should
    // be reviewed as model uncertainty, not a technical candidate.
    if (!after.slice(1, 4).some(frame => frame.features.offWristHeight <= ARM_THRESHOLDS.lowWristHeight + .1)) continue;
    if (candidates.length && current.time - candidates.at(-1).time < ARM_THRESHOLDS.eventSeparationSeconds) continue;
    const start = Math.max(0, (lowering ? raised.time : before[0].time) - .1);
    const end = Math.min(options.duration, swing.frame.time + .25);
    candidates.push({
      id: `aux-arm-${candidates.length + 1}`, start, end, time: current.time,
      reason: lowering ? '击球手出现上挥运动前后，辅助手腕从较高位置降到髋部附近；这是一处需要回看的候选。' :
        '击球手出现上挥运动时，辅助手腕连续处于髋部附近；请核对是否在准备或前挥阶段提前放低。',
      suggestion: '慢放这一拍，检查辅助手是否先帮助指向来球，再随前挥向后收肘；若画面是挥拍后的放松或其他动作，可忽略这条提示。',
      confidence: 'tentative',
      evidence: {
        ruleVersion: ARM_RULES_VERSION, kind: lowering ? 'lowering_during_swing' : 'low_during_swing',
        offHand: options.handedness === 'right' ? 'left' : 'right', mirrored: Boolean(options.mirrored),
        sampleTimes: [...new Set([raised.time, current.time, swing.frame.time])],
        raisedTime: lowering ? raised.time : null, lowTime: current.time,
        swingStart: current.time, swingEnd: swing.frame.time,
        offWristHeightTorso: f.offWristHeight, dropTorso: drop,
        hittingWristTravelTorso: swing.travel, phase: '前挥/上挥候选（未识别触球）',
      },
    });
  }
  return candidates;
}

export function analyzeArm(inputFrames, suppliedOptions = {}) {
  const options = {width: 0, height: 0, duration: 0, handedness: 'unknown', context: 'unknown', mirrored: false, target: null, targetTime: 0, ...suppliedOptions};
  const limitations = [
    '仅按二维肩、腕、髋位置筛选候选，未识别球拍、来球或触球时刻。',
    '工程阈值尚未经过教练校准；透视、遮挡、左右手关键点交换都可能造成误报或漏报。',
    '没有提示不代表动作合格，提示也不等于已确认的技术问题。',
  ];
  const validSize = finite(options.width) && finite(options.height) && options.width > 0 && options.height > 0;
  const ordered = (Array.isArray(inputFrames) ? inputFrames : []).filter(frame => finite(frame?.time) && frame.time >= 0)
    .sort((a, b) => a.time - b.time).filter((frame, i, array) => i === 0 || frame.time !== array[i - 1].time);
  if (!finite(options.duration) || options.duration <= 0) options.duration = ordered.at(-1)?.time ?? 0;
  const frames = ordered.filter(frame => frame.time <= options.duration + .001);
  const trackedFrames = frames.map(frame => ({time: frame.time, landmarks: null, features: null, status: 'untracked', reason: '尚未连续确认分析对象'}));
  const emptyResult = () => ({candidates: [], reviewIntervals: frames.length ? intervalsFromFrames(trackedFrames, options.duration) :
    [{start: 0, end: Math.max(0, options.duration), reason: '没有可用的姿态采样'}], trackedFrames,
    summary: {totalFrames: frames.length, usableFrames: 0, coverage: 0}, status: 'insufficient', limitations, ruleVersion: ARM_RULES_VERSION});
  if (!validSize || !frames.length) return emptyResult();
  const peopleByFrame = frames.map(frame => (Array.isArray(frame.poses) ? frame.poses : []).map(pose => body(pose, options.width, options.height)).filter(Boolean));
  const targetValid = options.target && finite(options.target.x) && finite(options.target.y) && options.target.x >= 0 &&
    options.target.x <= 1 && options.target.y >= 0 && options.target.y <= 1;
  let anchor = -1, anchorPerson = null;
  if (targetValid) {
    const targetTime = finite(options.targetTime) ? options.targetTime : 0;
    const indices = frames.map((frame, index) => ({index, dt: Math.abs(frame.time - targetTime)}))
      .filter(item => item.dt <= .35).sort((a, b) => a.dt - b.dt);
    for (const item of indices) {
      const chosen = chooseInitial(peopleByFrame[item.index], options.target, options.width, options.height);
      trackedFrames[item.index].reason = chosen.reason;
      if (chosen.person) {anchor = item.index; anchorPerson = chosen.person; break;}
    }
  } else {
    for (let i = 0; i < frames.length; i++) {
      const chosen = chooseInitial(peopleByFrame[i], null, options.width, options.height);
      trackedFrames[i].reason = chosen.reason;
      if (chosen.person) {anchor = i; anchorPerson = chosen.person; break;}
      // A visible ambiguity must be resolved by selecting the person, not by
      // silently adopting whichever person becomes alone later in the clip.
      if (peopleByFrame[i].length > 1) break;
    }
  }
  if (anchor < 0) {
    const reason = trackedFrames.find(frame => frame.reason && frame.reason !== '尚未连续确认分析对象')?.reason ?? '所选时刻无法确认分析对象，请重新选择';
    trackedFrames.forEach(frame => {frame.reason = reason;});
    return emptyResult();
  }
  function setFrame(index, person) {
    const f = features(person, options);
    trackedFrames[index] = {time: frames[index].time, landmarks: person.pose, features: f,
      status: f ? 'usable' : 'unreliable_limbs', reason: f ? '' : '肩或手腕关键点置信度不足或超出画面'};
  }
  setFrame(anchor, anchorPerson);
  for (const direction of [1, -1]) {
    let previous = anchorPerson, previousTime = frames[anchor].time, identityLost = false;
    for (let i = anchor + direction; i >= 0 && i < frames.length; i += direction) {
      const dt = Math.abs(frames[i].time - previousTime);
      if (identityLost || dt > ARM_THRESHOLDS.maxIdentityGapSeconds) {
        identityLost = true;
        trackedFrames[i].reason = '跟踪中断后无法确认仍是同一人，请分段或重新选择分析对象';
        continue;
      }
      const chosen = associate(peopleByFrame[i], previous, dt);
      if (!chosen.person) {trackedFrames[i].reason = chosen.reason; continue;}
      setFrame(i, chosen.person); previous = chosen.person; previousTime = frames[i].time;
    }
  }
  const usableFrames = trackedFrames.filter(frame => frame.status === 'usable').length;
  const summary = {totalFrames: frames.length, usableFrames, coverage: usableFrames / frames.length};
  const reviewIntervals = intervalsFromFrames(trackedFrames, options.duration);
  const contextReasons = [];
  if (!['left', 'right'].includes(options.handedness)) contextReasons.push('请先确认持拍手，才能区分辅助手与击球手');
  if (options.context !== 'forehand') contextReasons.push(options.context === 'self_feed' ?
    '自抛球会自然放低辅助手，本版本只显示跟踪结果，不提示技术问题' : options.context === 'mixed' ?
      '片段混有其他击球动作，请截取连续正手片段后再判断' : '请先确认这是连续正手练习片段');
  if (contextReasons.length) return {candidates: [], reviewIntervals, trackedFrames, summary, status: 'context_required', limitations: [...contextReasons, ...limitations], ruleVersion: ARM_RULES_VERSION};
  if (usableFrames < 5 || summary.coverage < .3) return {candidates: [], reviewIntervals, trackedFrames, summary, status: 'insufficient', limitations, ruleVersion: ARM_RULES_VERSION};
  const candidates = screenCandidates(trackedFrames, options);
  return {candidates, reviewIntervals, trackedFrames, summary, status: candidates.length ? 'candidates' : 'no_candidates', limitations, ruleVersion: ARM_RULES_VERSION};
}
