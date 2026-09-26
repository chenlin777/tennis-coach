import { scanPoses } from './pose_analysis.js';
import { analyzeArm, ARM_RULES_VERSION } from './arm_rules.js';

const $ = (id) => document.getElementById(id);
const video = $('sourceVideo');
const canvas = $('previewCanvas');
const ctx = canvas.getContext('2d');
const MAX_SECONDS = 60;
const state = {
  file: null, url: null, ready: false, epoch: 0, controller: null,
  poses: null, result: null, resultOptions: null, target: null, targetTime: 0,
  selecting: false, keyboardPoint: { x: 0.5, y: 0.5 }, animation: null,
  loadId: 0, metadataController: null, metadataReadyLoadId: null, measuredDuration: null,
  firstFrameCallback: null,
};
const clock = (seconds) => {
  const n = Math.max(0, Number(seconds) || 0);
  return `${String(Math.floor(n / 60)).padStart(2, '0')}:${(n % 60).toFixed(1).padStart(4, '0')}`;
};
const clamp = (n, low, high) => Math.max(low, Math.min(high, n));
const finite = (n) => Number.isFinite(n);

function options() {
  return {
    handedness: $('handedness').value,
    context: $('practiceContext').value,
    mirrored: $('mirrored').checked,
    target: state.target ? { ...state.target } : null,
    targetTime: state.targetTime,
  };
}

function clearResult() {
  state.result = null;
  state.resultOptions = null;
  $('candidateList').replaceChildren();
  $('reviewList').replaceChildren();
  $('limitations').replaceChildren();
  $('reviewDetails').hidden = true;
  $('coverage').textContent = '';
  $('resultsStatus').textContent = '分析完成后显示结果。';
  $('exportReport').disabled = true;
}

// Every async inference callback belongs to an epoch. Even a late result from an
// inference implementation which ignores AbortSignal cannot restore stale data.
function invalidate({ keepPoses = false } = {}) {
  state.epoch += 1;
  state.controller?.abort();
  state.controller = null;
  if (!keepPoses) state.poses = null;
  clearResult();
  $('analysisProgress').hidden = true;
  $('analysisProgress').value = 0;
  $('cancelAnalysis').hidden = true;
  updateControls();
  draw();
}

function updateControls() {
  const playable = !!state.file && finite(video.duration) && video.duration > 0;
  for (const id of ['playPause', 'timeline', 'playbackRate']) $(id).disabled = !playable;
  $('analyzeButton').disabled = !state.ready || !!state.controller;
  $('chooseTarget').disabled = !state.ready;
  $('clearTarget').disabled = !state.target;
  $('chooseTarget').setAttribute('aria-pressed', String(state.selecting));
  $('chooseTarget').textContent = state.selecting ? '取消选择人物' : '选择分析人物';
  $('stage').classList.toggle('selecting', state.selecting);
}

function nearestFrame() {
  const frames = state.result?.trackedFrames;
  if (!frames?.length) return null;
  let low = 0, high = frames.length;
  while (low < high) {
    const mid = Math.floor((low + high) / 2);
    if (frames[mid].time < video.currentTime) low = mid + 1;
    else high = mid;
  }
  const choices = [frames[low], frames[low - 1]].filter(Boolean);
  choices.sort((a, b) => Math.abs(a.time - video.currentTime) - Math.abs(b.time - video.currentTime));
  const frame = choices[0];
  // Do not carry an old skeleton into a tracking gap or interpolate a stroke.
  const tolerance = Math.max(0.075, 0.75 / (state.poses?.sampleFps || 12));
  return frame && Math.abs(frame.time - video.currentTime) <= tolerance ? frame : null;
}

function visible(point) {
  return point && finite(point.x) && finite(point.y) && point.x >= 0 && point.x <= 1 &&
    point.y >= 0 && point.y <= 1 && (point.visibility ?? 1) >= 0.55 && (point.presence ?? 1) >= 0.55;
}

function drawCross(point, color) {
  const x = point.x * canvas.width, y = point.y * canvas.height;
  const radius = Math.max(9, canvas.width / 55);
  ctx.strokeStyle = color;
  ctx.lineWidth = Math.max(2, canvas.width / 400);
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.moveTo(x - radius * 1.5, y); ctx.lineTo(x + radius * 1.5, y);
  ctx.moveTo(x, y - radius * 1.5); ctx.lineTo(x, y + radius * 1.5);
  ctx.stroke();
}

function draw() {
  if (!video.videoWidth || video.readyState < 2) return;
  if (canvas.width !== video.videoWidth || canvas.height !== video.videoHeight) {
    canvas.width = video.videoWidth; canvas.height = video.videoHeight;
  }
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  const frame = nearestFrame();
  const landmarks = frame?.landmarks;
  if ($('showSkeleton').checked && landmarks) {
    const selectedOptions = state.resultOptions || options();
    let leftOff = selectedOptions.handedness === 'right';
    if (selectedOptions.mirrored) leftOff = !leftOff;
    const offIndices = new Set(selectedOptions.handedness === 'unknown' ? [] : (leftOff ? [11, 13, 15] : [12, 14, 16]));
    const edges = [[11, 12], [11, 13], [13, 15], [12, 14], [14, 16], [11, 23], [12, 24], [23, 24]];
    ctx.lineWidth = Math.max(2, canvas.width / 350);
    for (const [a, b] of edges) {
      if (!visible(landmarks[a]) || !visible(landmarks[b])) continue;
      ctx.strokeStyle = offIndices.has(a) && offIndices.has(b) ? '#ffbf47' : '#56e6cd';
      ctx.beginPath();
      ctx.moveTo(landmarks[a].x * canvas.width, landmarks[a].y * canvas.height);
      ctx.lineTo(landmarks[b].x * canvas.width, landmarks[b].y * canvas.height);
      ctx.stroke();
    }
    for (const i of [11, 12, 13, 14, 15, 16, 23, 24]) {
      const point = landmarks[i];
      if (!visible(point)) continue;
      ctx.beginPath();
      ctx.arc(point.x * canvas.width, point.y * canvas.height, Math.max(3, canvas.width / 190), 0, Math.PI * 2);
      ctx.fillStyle = offIndices.has(i) ? '#ffbf47' : '#56e6cd';
      ctx.fill();
      ctx.strokeStyle = '#183b35'; ctx.lineWidth = 1; ctx.stroke();
    }
  }
  if (state.selecting) drawCross(state.keyboardPoint, '#ffbf47');
  else if (state.target && Math.abs(video.currentTime - state.targetTime) < 0.15) drawCross(state.target, '#ffbf47');
  if (!state.result) {
    $('frameStatus').textContent = '分析后，关键点会叠加在对应画面上。点位缺失时不补画动作。';
  } else if (!landmarks) {
    $('frameStatus').textContent = '当前时刻缺少可靠的目标姿态，请直接查看原画面。';
  } else if (frame.status && frame.status !== 'usable') {
    $('frameStatus').textContent = `约 ${clock(frame.time)} 的部分关键点不可靠，缺失点位没有补画。请直接核对原画面。`;
  } else {
    const legend = state.resultOptions?.handedness === 'unknown' ? '尚未确认持拍手，绿色显示可见点位' : '黄色为辅助手，绿色为其余点位';
    $('frameStatus').textContent = `显示约 ${clock(frame.time)} 的采样姿态；${legend}。关键点也可能跟错，请核对人物和手腕。`;
  }
  $('timeline').value = String(video.currentTime);
  $('timeDisplay').textContent = `${clock(video.currentTime)} / ${clock(video.duration)}`;
}

function tick() {
  state.animation = null;
  draw();
  if (!video.paused && !video.ended) state.animation = requestAnimationFrame(tick);
}

function stopAnimation() {
  if (state.animation !== null) cancelAnimationFrame(state.animation);
  state.animation = null;
}

function seek(time) {
  if (!state.ready) return;
  video.pause();
  video.currentTime = clamp(Number(time) || 0, 0, Math.max(0, video.duration - 0.001));
  draw();
}

function seekButton(time, label) {
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = label;
  button.dataset.time = String(time);
  button.addEventListener('click', () => seek(time));
  return button;
}

function paragraph(text, className = '') {
  const p = document.createElement('p');
  p.textContent = text;
  if (className) p.className = className;
  return p;
}

function renderResult() {
  const result = state.result;
  if (!result) return;
  const messages = {
    candidates: `发现 ${result.candidates.length} 个辅助手下落候选。请慢放核实，这些还不是动作错误的定论。`,
    no_candidates: '未检出辅助手下落候选。未检出不代表动作合格，也可能漏掉动作。',
    insufficient: '画面不足，暂时无法给出辅助手候选。请检查人物选择、遮挡与身体是否完整入镜。',
    context_required: '请确认持拍手和连续正手练习场景，才会生成辅助手候选。混合动作和自抛球暂时只供复看。',
  };
  $('resultsStatus').textContent = messages[result.status] || '已完成初步分析，请查看画面证据。';
  const summary = result.summary || {};
  const coverage = finite(summary.coverage) ? `${(summary.coverage * 100).toFixed(0)}%` : '未知';
  $('coverage').textContent = `可用姿态 ${summary.usableFrames ?? 0} / ${summary.totalFrames ?? 0} 个采样画面（${coverage}）。这是画面可用情况，不是动作合格率。`;
  const cards = (result.candidates || []).map((candidate, index) => {
    const li = document.createElement('li'); li.className = 'candidate';
    const top = document.createElement('div'); top.className = 'candidate-top';
    top.append(seekButton(candidate.time, `查看 ${clock(candidate.time)} 附近`));
    const badge = document.createElement('span'); badge.className = 'badge'; badge.textContent = `候选 ${index + 1} · 待复核`;
    top.append(badge); li.append(top);
    li.append(paragraph(candidate.reason || '辅助手位置需要结合画面复看。'));
    if (candidate.suggestion) li.append(paragraph(`复看建议：${candidate.suggestion}`));
    const evidence = candidate.evidence || {};
    const range = `${clock(candidate.start)}–${clock(candidate.end)}`;
    const sampleTimes = Array.isArray(evidence.sampleTimes) ? evidence.sampleTimes.filter(finite) : [];
    li.append(paragraph(`片段 ${range}；${evidence.phase || '挥拍候选，未识别触球'}。`, 'evidence'));
    const actions = document.createElement('div'); actions.className = 'button-row';
    actions.append(seekButton(candidate.start, '从片段开头复看'));
    for (const time of [...new Set(sampleTimes)].slice(0, 3)) actions.append(seekButton(time, `证据 ${clock(time)}`));
    li.append(actions);
    return li;
  });
  $('candidateList').replaceChildren(...cards);
  const intervals = (result.reviewIntervals || []).map((interval) => {
    const li = document.createElement('li');
    li.append(seekButton(interval.start, `${clock(interval.start)}–${clock(interval.end)}`));
    li.append(document.createTextNode(interval.reason || '这一段需要人工复看。'));
    return li;
  });
  $('reviewList').replaceChildren(...intervals);
  $('reviewDetails').hidden = intervals.length === 0;
  const limitations = (result.limitations || []).map((text) => {
    const li = document.createElement('li'); li.textContent = String(text); return li;
  });
  $('limitations').replaceChildren(...limitations);
  $('exportReport').disabled = false;
  draw();
}

function applyRules() {
  if (!state.poses || !state.ready) return;
  const selectedOptions = options();
  const poses = state.poses;
  try {
    const result = analyzeArm(poses.frames, {
      ...selectedOptions, width: poses.width, height: poses.height, duration: poses.duration,
    });
    state.result = result;
    state.resultOptions = selectedOptions;
    renderResult();
    $('analysisStatus').textContent = '已完成。可调整持拍手、场景或人物选择，立即更新提示。';
    $('status').textContent = '报告是待教练复核的初步提示；没有检出候选，不代表动作合格。';
  } catch (error) {
    clearResult();
    $('analysisStatus').textContent = `无法整理动作提示：${error.message || '未知错误'}`;
  }
}

function settingsChanged() {
  const cached = state.poses;
  invalidate({ keepPoses: !!cached });
  if (cached) applyRules();
  else $('analysisStatus').textContent = state.ready ? '设置已改变，请重新开始分析。' : '选择视频后开始分析。';
}

function currentLoad(loadId, file, url) {
  return state.loadId === loadId && state.file === file && state.url === url &&
    video.src === url && video.currentSrc === url;
}

function prepareFirstFrame(loadId, file, url) {
  if (state.firstFrameCallback !== null && video.cancelVideoFrameCallback) {
    video.cancelVideoFrameCallback(state.firstFrameCallback);
  }
  state.firstFrameCallback = null;
  if (!video.requestVideoFrameCallback) return;
  const callback = video.requestVideoFrameCallback(() => {
    if (state.firstFrameCallback === callback) state.firstFrameCallback = null;
    // loadeddata can arrive before drawImage sees a presented frame. Repaint
    // after the first actual frame, without seeking or playing the user's clip.
    if (currentLoad(loadId, file, url)) draw();
  });
  state.firstFrameCallback = callback;
}

function probeDuration(loadId, file, url, signal) {
  // MediaRecorder WebMs can omit duration metadata. Seek beyond the last frame
  // to let the decoder measure it, then return to zero before enabling analysis.
  // The requested seek position is never used as the measured duration.
  return new Promise((resolve, reject) => {
    let returning = false, settled = false, timer;
    const events = ['durationchange', 'seeked', 'timeupdate', 'loadeddata', 'canplay'];
    const finish = (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      for (const name of events) video.removeEventListener(name, check);
      video.removeEventListener('error', failed);
      signal.removeEventListener('abort', aborted);
      if (error) reject(error); else resolve();
    };
    const aborted = () => finish(new DOMException('视频已切换', 'AbortError'));
    const failed = () => finish(new Error('无法读取视频时长'));
    const check = () => {
      if (signal.aborted || !currentLoad(loadId, file, url)) { aborted(); return; }
      if (!finite(video.duration) || video.duration <= 0) return;
      if (!returning) {
        returning = true;
        try { video.currentTime = 0; } catch { failed(); return; }
      }
      if (!video.seeking && video.currentTime < 0.05 && video.readyState >= 2) finish();
    };
    if (signal.aborted || !currentLoad(loadId, file, url)) { aborted(); return; }
    for (const name of events) video.addEventListener(name, check);
    video.addEventListener('error', failed);
    signal.addEventListener('abort', aborted, { once: true });
    timer = setTimeout(failed, 20000);
    try { video.preload = 'auto'; video.currentTime = 1e10; check(); } catch { failed(); }
  });
}

function acceptMetadata(loadId, file, url) {
  if (!currentLoad(loadId, file, url)) return;
  const valid = finite(video.duration) && video.duration > 0 && video.videoWidth > 0 && video.videoHeight > 0;
  state.ready = valid && video.duration <= MAX_SECONDS;
  state.metadataReadyLoadId = loadId;
  state.measuredDuration = video.duration;
  video.playbackRate = Number($('playbackRate').value);
  $('timeline').max = String(valid ? video.duration : 1);
  $('fileMeta').textContent = valid ? `${video.duration.toFixed(1)} 秒 · ${video.videoWidth} × ${video.videoHeight} · ${(file.size / 1048576).toFixed(1)} MB` : '无法读取有效的视频长度。';
  $('analysisStatus').textContent = !valid ? '请换一个浏览器可以播放的视频。' : !state.ready ? '当前片段超过 60 秒，请先剪成 10–20 秒的短片再分析。' : '可以开始分析；确认持拍手和场景后，才能给出动作候选。';
  updateControls(); draw();
}

$('videoFile').addEventListener('change', () => {
  const file = $('videoFile').files[0];
  if (!file) return;
  state.loadId += 1;
  state.metadataController?.abort(); state.metadataController = null;
  state.metadataReadyLoadId = null; state.measuredDuration = null;
  invalidate();
  video.pause(); stopAnimation();
  state.ready = false;
  state.file = file;
  state.target = null; state.targetTime = 0; state.selecting = false;
  if (state.url) URL.revokeObjectURL(state.url);
  state.url = URL.createObjectURL(file);
  $('fileName').textContent = file.name;
  $('fileMeta').textContent = '正在读取视频信息…';
  $('targetStatus').textContent = '有旁观者或画中画时，请暂停并点选击球者的躯干。建议在片头选择；分析后核对关键点是否跟对人。';
  $('analysisStatus').textContent = '正在读取视频…';
  $('status').textContent = '视频仅在本机读取，不会自动上传或导出。';
  $('emptyPreview').hidden = true; $('stage').hidden = false;
  video.src = state.url;
  video.preload = 'metadata';
  prepareFirstFrame(state.loadId, file, state.url);
  video.load();
  $('timeline').value = '0'; $('timeDisplay').textContent = '00:00.0 / 00:00.0';
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  updateControls();
});

video.addEventListener('loadedmetadata', async () => {
  const { loadId, file, url } = state;
  if (!file || !currentLoad(loadId, file, url)) return;
  if (!finite(video.duration) && video.videoWidth > 0 && video.videoHeight > 0) {
    state.metadataController?.abort();
    const controller = new AbortController(); state.metadataController = controller;
    state.ready = false; updateControls();
    $('fileMeta').textContent = '正在读取视频长度…';
    $('analysisStatus').textContent = '此视频未记录时长，正在本机读取结尾并返回开头。仍可切换到其他视频。';
    try { await probeDuration(loadId, file, url, controller.signal); }
    catch (error) {
      if (controller.signal.aborted || !currentLoad(loadId, file, url)) return;
      $('fileMeta').textContent = '视频长度读取失败';
      $('analysisStatus').textContent = '未能确认视频时长，请换用普通 MP4 或其他可播放的视频。';
      return;
    } finally {
      if (state.metadataController === controller) state.metadataController = null;
    }
    if (controller.signal.aborted || !currentLoad(loadId, file, url)) return;
  }
  acceptMetadata(loadId, file, url);
});

video.addEventListener('durationchange', () => {
  const { loadId, file, url } = state;
  if (state.metadataReadyLoadId !== loadId || !currentLoad(loadId, file, url)) return;
  if (state.measuredDuration === video.duration) return;
  // A later duration correction must not leave a now-overlong file analyzable.
  invalidate();
  acceptMetadata(loadId, file, url);
});

video.addEventListener('error', () => {
  if (!state.file) return;
  state.metadataController?.abort(); state.metadataController = null;
  state.metadataReadyLoadId = null;
  invalidate(); state.ready = false;
  $('analysisStatus').textContent = '浏览器无法读取这个视频。可先用 prepare-clips.cmd 转成 MP4 短片。';
  updateControls();
});
for (const event of ['loadeddata', 'seeked', 'timeupdate']) video.addEventListener(event, draw);
video.addEventListener('play', () => { $('playPause').textContent = '暂停'; stopAnimation(); tick(); });
video.addEventListener('pause', () => { $('playPause').textContent = '播放'; stopAnimation(); draw(); });
video.addEventListener('ended', () => { $('playPause').textContent = '播放'; stopAnimation(); draw(); });
$('playPause').addEventListener('click', async () => {
  if (!video.paused) video.pause();
  else {
    state.selecting = false; updateControls();
    try { await video.play(); } catch { $('status').textContent = '暂时无法播放，请重新选择视频。'; }
  }
});
$('timeline').addEventListener('input', () => { video.pause(); video.currentTime = Number($('timeline').value); });
$('playbackRate').addEventListener('change', () => { video.playbackRate = Number($('playbackRate').value); });
$('showSkeleton').addEventListener('change', draw);
for (const id of ['handedness', 'practiceContext', 'mirrored']) $(id).addEventListener('change', settingsChanged);

$('chooseTarget').addEventListener('click', () => {
  state.selecting = !state.selecting;
  video.pause();
  $('targetStatus').textContent = state.selecting ? '点击要分析的人的躯干，不要点球拍或背景。也可以用方向键移动十字，再按回车确认。' : state.target ? `已在 ${clock(state.targetTime)} 选择人物。可重新点选，或清除后自动选择。` : '单人可直接分析；多人时请点选目标的躯干。';
  updateControls(); draw();
  if (state.selecting) canvas.focus();
});

function selectTarget(point) {
  state.target = point;
  state.targetTime = video.currentTime;
  state.selecting = false;
  $('targetStatus').textContent = `已在 ${clock(state.targetTime)} 选择人物。请检查分析后的关键点是否跟对人。`;
  settingsChanged(); updateControls(); draw();
}

canvas.addEventListener('click', (event) => {
  if (!state.selecting || !state.ready) return;
  const rect = canvas.getBoundingClientRect();
  const scale = Math.min(rect.width / canvas.width, rect.height / canvas.height);
  const width = canvas.width * scale, height = canvas.height * scale;
  const x = (event.clientX - rect.left - (rect.width - width) / 2) / width;
  const y = (event.clientY - rect.top - (rect.height - height) / 2) / height;
  if (x < 0 || x > 1 || y < 0 || y > 1) return;
  selectTarget({ x, y });
});

canvas.addEventListener('keydown', (event) => {
  if (!state.selecting || !state.ready) return;
  const movements = { ArrowLeft: [-0.02, 0], ArrowRight: [0.02, 0], ArrowUp: [0, -0.02], ArrowDown: [0, 0.02] };
  if (movements[event.key]) {
    event.preventDefault();
    const [dx, dy] = movements[event.key];
    state.keyboardPoint = { x: clamp(state.keyboardPoint.x + dx, 0, 1), y: clamp(state.keyboardPoint.y + dy, 0, 1) };
    draw();
  } else if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault(); selectTarget({ ...state.keyboardPoint });
  } else if (event.key === 'Escape') {
    state.selecting = false; updateControls(); draw();
  }
});

$('clearTarget').addEventListener('click', () => {
  state.target = null; state.targetTime = 0; state.selecting = false;
  $('targetStatus').textContent = '已清除人物选择。多人画面请重新点选，避免跟错人。';
  settingsChanged(); updateControls(); draw();
});

$('analyzeButton').addEventListener('click', async () => {
  if (!state.ready || state.controller) return;
  invalidate();
  video.pause(); state.selecting = false;
  const epoch = state.epoch;
  const file = state.file;
  const controller = new AbortController(); state.controller = controller;
  $('cancelAnalysis').hidden = false;
  $('analysisProgress').hidden = false;
  $('analysisStatus').textContent = '正在准备本地姿态模型…';
  updateControls();
  try {
    const poses = await scanPoses(file, {
      signal: controller.signal,
      sampleFps: 12,
      onProgress(progress) {
        if (state.epoch !== epoch || controller.signal.aborted) return;
        const fraction = finite(progress.fraction) ? clamp(progress.fraction, 0, 1) : 0;
        $('analysisProgress').value = fraction * 100;
        $('analysisStatus').textContent = progress.phase === 'loading' ? '正在加载本地姿态模型…' : `正在逐段分析：${clock(progress.time)} / ${clock(progress.duration)}（${Math.round(fraction * 100)}%）`;
      },
    });
    if (state.epoch !== epoch || controller.signal.aborted || state.file !== file) return;
    state.poses = poses;
    applyRules();
    $('analysisProgress').value = 100;
  } catch (error) {
    if (state.epoch !== epoch) return;
    clearResult();
    $('analysisStatus').textContent = error.name === 'AbortError' ? '已取消分析，可以重新开始。' : `分析未完成：${error.message || '未知错误'}。请确认已运行 setup-auto.cmd。`;
  } finally {
    if (state.epoch === epoch) {
      state.controller = null;
      $('cancelAnalysis').hidden = true;
      $('analysisProgress').hidden = true;
      updateControls(); draw();
    }
  }
});

$('cancelAnalysis').addEventListener('click', () => {
  invalidate();
  $('analysisStatus').textContent = '已取消分析，可以重新开始。';
});

$('exportReport').addEventListener('click', () => {
  if (!state.result || !state.poses || !state.file || !state.resultOptions) return;
  const report = {
    schemaVersion: '0.1', kind: 'candidate_review', requiresCoachReview: true,
    createdAt: new Date().toISOString(),
    file: {
      name: state.file.name, size: state.file.size, type: state.file.type,
      lastModified: state.file.lastModified, duration: state.poses.duration,
      width: state.poses.width, height: state.poses.height,
    },
    options: state.resultOptions, modelVersion: state.poses.modelVersion,
    rulesVersion: state.result.ruleVersion || ARM_RULES_VERSION,
    sourceIdentity: 'file_metadata_only_not_content_verified',
    sampleFps: state.poses.sampleFps, result: state.result,
  };
  const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${state.file.name.replace(/\.[^.]+$/, '').replace(/[<>:"/\\|?*\x00-\x1f]/g, '_')}-arm-review.json`;
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
  $('status').textContent = '已导出这段视频的初步报告，包含时间依据和当前设置；尚未作为教练确认标签。';
});

window.addEventListener('pagehide', () => {
  state.epoch += 1;
  state.loadId += 1; state.metadataController?.abort();
  if (state.firstFrameCallback !== null && video.cancelVideoFrameCallback) video.cancelVideoFrameCallback(state.firstFrameCallback);
  state.controller?.abort(); stopAnimation();
  if (state.url) URL.revokeObjectURL(state.url);
});
