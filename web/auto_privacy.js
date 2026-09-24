// Local inference only. Runtime and model files are installed by setup-auto.cmd.
export const MODEL_VERSION = "mediapipe-1.0.1/blazeface-full-v1/pose-lite-v1";
let modelsPromise = null;
let loadedModels = null;
let scanQueue = Promise.resolve();
let lastPoseTimestamp = 0;

function cancelled() { return new DOMException("已取消自动分析", "AbortError"); }
function check(signal) { if (signal?.aborted) throw cancelled(); }
const clamp = (n, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, n));

function clipped(rect) {
  const left = clamp(rect.x), top = clamp(rect.y);
  return { ...rect, x: left, y: top, w: Math.max(0, clamp(rect.x + rect.w) - left), h: Math.max(0, clamp(rect.y + rect.h) - top) };
}

function union(a, b) {
  const x = Math.min(a.x, b.x), y = Math.min(a.y, b.y);
  return clipped({ x, y, w: Math.max(a.x + a.w, b.x + b.w) - x, h: Math.max(a.y + a.h, b.y + b.h) - y });
}

function overlap(a, b) {
  const area = Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)) *
    Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
  return area / Math.max(1e-8, Math.min(a.w * a.h, b.w * b.h));
}

export function mergeBoxes(boxes) {
  const result = [];
  for (const input of boxes) {
    let box = clipped(input);
    if (box.w <= 0 || box.h <= 0) continue;
    for (let i = result.length - 1; i >= 0; i--) {
      if (overlap(box, result[i]) > 0.2) {
        box = { ...union(box, result[i]), source: "combined" };
        result.splice(i, 1);
      }
    }
    result.push(box);
  }
  return result;
}

export function headBoxFromPose(landmarks, width, height) {
  const visible = (p) => p && Number.isFinite(p.x) && Number.isFinite(p.y) && (p.visibility ?? 1) >= 0.35;
  const head = landmarks.slice(0, 11).filter(visible);
  if (head.length < 3 || !visible(landmarks[11]) || !visible(landmarks[12])) return null;
  const xs = head.map((p) => p.x * width), ys = head.map((p) => p.y * height);
  const left = Math.min(...xs), right = Math.max(...xs), top = Math.min(...ys), bottom = Math.max(...ys);
  const shoulders = [landmarks[11], landmarks[12]];
  const shoulderWidth = Math.hypot((shoulders[0].x - shoulders[1].x) * width, (shoulders[0].y - shoulders[1].y) * height);
  let torso = 0;
  if (visible(landmarks[23]) && visible(landmarks[24])) {
    torso = Math.hypot((shoulders[0].x + shoulders[1].x - landmarks[23].x - landmarks[24].x) * width / 2,
      (shoulders[0].y + shoulders[1].y - landmarks[23].y - landmarks[24].y) * height / 2);
  }
  // Face landmarks do not include the crown. Use a deliberately larger head
  // region, including a body-size floor when the person turns sideways.
  const side = Math.max((right - left) * 1.6, (bottom - top) * 2.2, shoulderWidth * 0.65, torso * 0.48, Math.min(width, height) * 0.035);
  if (side > Math.max(width, height) * 0.5) return null;
  const cx = (left + right) / 2, cy = (top + bottom) / 2 - side * 0.12;
  return clipped({ x: (cx - side * 0.65) / width, y: (cy - side * 0.65) / height,
    w: side * 1.3 / width, h: side * 1.3 / height, source: "pose-head" });
}

async function initializeModels() {
  if (!modelsPromise) {
    modelsPromise = (async () => {
      const { FilesetResolver, FaceDetector, PoseLandmarker } = await import("./vendor/mediapipe/vision_bundle.mjs");
      const files = await FilesetResolver.forVisionTasks("./vendor/mediapipe/wasm");
      let face;
      try {
        face = await FaceDetector.createFromOptions(files, {
          baseOptions: { modelAssetPath: "./vendor/models/blaze_face_full_range.tflite", delegate: "CPU" },
          runningMode: "IMAGE", minDetectionConfidence: 0.4, minSuppressionThreshold: 0.3,
        });
        const pose = await PoseLandmarker.createFromOptions(files, {
          baseOptions: { modelAssetPath: "./vendor/models/pose_landmarker_lite.task", delegate: "CPU" },
          runningMode: "IMAGE", numPoses: 4, minPoseDetectionConfidence: 0.4,
          minPosePresenceConfidence: 0.4, outputSegmentationMasks: false,
        });
        loadedModels = { face, pose };
        return loadedModels;
      } catch (error) { if (face) face.close(); throw error; }
    })().catch((error) => {
      modelsPromise = null;
      throw new Error(`自动模型未能加载。请先运行 setup-auto.cmd 安装本地资源，再重试。${error.message ? `（${error.message}）` : ""}`);
    });
  }
  return modelsPromise;
}

export function disposeModels() {
  if (loadedModels) {
    loadedModels.face.close();
    loadedModels.pose.close();
  }
  loadedModels = null;
  modelsPromise = null;
}

function eventOnce(target, name, signal, timeoutMs = 20000) {
  return new Promise((resolve, reject) => {
    let timer;
    const cleanup = () => {
      clearTimeout(timer);
      target.removeEventListener(name, done);
      target.removeEventListener("error", fail);
      signal?.removeEventListener("abort", abort);
    };
    const done = () => { cleanup(); resolve(); };
    const fail = () => { cleanup(); reject(new Error("无法解码视频，请换用浏览器支持的 MP4 或 WebM。")); };
    const abort = () => { cleanup(); reject(cancelled()); };
    if (signal?.aborted) { abort(); return; }
    target.addEventListener(name, done, { once: true });
    target.addEventListener("error", fail, { once: true });
    signal?.addEventListener("abort", abort, { once: true });
    timer = setTimeout(() => { cleanup(); reject(new Error("视频解码等待超时，请用较短视频重试。")); }, timeoutMs);
  });
}

function decodedFrame(video, signal) {
  if (!video.requestVideoFrameCallback) return Promise.resolve();
  return new Promise((resolve, reject) => {
    let id, timer;
    const cleanup = () => { clearTimeout(timer); if (id !== undefined) video.cancelVideoFrameCallback(id); signal?.removeEventListener("abort", abort); };
    const abort = () => { cleanup(); reject(cancelled()); };
    if (signal?.aborted) { abort(); return; }
    signal?.addEventListener("abort", abort, { once: true });
    id = video.requestVideoFrameCallback(() => { cleanup(); resolve(); });
    timer = setTimeout(() => { cleanup(); reject(new Error("未能读取分析帧，请重试或缩短视频。")); }, 20000);
  });
}

async function seekFrame(video, time, signal) {
  check(signal);
  if (Math.abs(video.currentTime - time) < 1e-5 && !video.seeking && video.readyState >= 2) return;
  const presented = decodedFrame(video, signal);
  const sought = eventOnce(video, "seeked", signal);
  video.currentTime = time;
  await Promise.all([presented, sought]);
  check(signal);
}

function faceBoxes(face, canvas) {
  return face.detect(canvas).detections.map((detection) => {
    const b = detection.boundingBox;
    // A 35% margin on each side also protects the frame between two samples.
    return clipped({ x: (b.originX - b.width * 0.35) / canvas.width,
      y: (b.originY - b.height * 0.5) / canvas.height,
      w: b.width * 1.7 / canvas.width, h: b.height * 1.85 / canvas.height, source: "face" });
  });
}

export function buildTimeline(frames, duration, modelVersion = MODEL_VERSION) {
  const tracks = [];
  let id = 0;
  for (const frame of frames) {
    const used = new Set();
    for (const box of frame.boxes) {
      let best = null, bestDistance = Infinity;
      for (const track of tracks) {
        const last = track.points[track.points.length - 1];
        if (used.has(track.id) || frame.time - last.time > 0.45) continue;
        const distance = Math.hypot((box.x + box.w / 2) - (last.box.x + last.box.w / 2),
          (box.y + box.h / 2) - (last.box.y + last.box.h / 2));
        const tolerance = Math.max(box.w, box.h, last.box.w, last.box.h) * 1.8;
        if (distance < tolerance && distance < bestDistance) { best = track; bestDistance = distance; }
      }
      if (!best) { best = { id: id++, points: [] }; tracks.push(best); }
      best.points.push({ time: frame.time, box });
      used.add(best.id);
    }
  }
  const reviewIntervals = [];
  for (let i = 0; i < frames.length; i++) {
    if (frames[i].boxes.length) continue;
    const start = Math.max(0, frames[i].time - 0.1);
    const end = Math.min(duration, (frames[i + 1]?.time ?? duration) + 0.1);
    const previous = reviewIntervals[reviewIntervals.length - 1];
    if (previous && start <= previous.end + 0.15) previous.end = end;
    else reviewIntervals.push({ start, end, reason: "没有直接检测到脸或头部，请检查是否漏遮（短间隔可能已补遮）。" });
  }
  return { frames, tracks, duration, reviewIntervals, detectedFrames: frames.filter((f) => f.boxes.length).length,
    totalFrames: frames.length, modelVersion };
}

export function getMasksAt(analysis, time) {
  if (!analysis || !Number.isFinite(time) || time < 0 || time > analysis.duration + 0.1) return [];
  const result = [];
  for (const track of analysis.tracks) {
    const points = track.points;
    let low = 0, high = points.length;
    while (low < high) {
      const mid = (low + high) >> 1;
      if (points[mid].time < time) low = mid + 1;
      else high = mid;
    }
    const after = points[low], before = points[low - 1];
    if (after && Math.abs(after.time - time) < 1e-5) result.push(after.box);
    else if (before && after && after.time - before.time <= 0.45) {
      // Cover both endpoints instead of smoothing inward and exposing edges.
      result.push(union(before.box, after.box));
    } else if (before && time - before.time <= 0.12) result.push(before.box);
    else if (after && after.time - time <= 0.12) result.push(after.box);
  }
  return mergeBoxes(result);
}

async function runAnalysis(file, { signal, onProgress = () => {}, sampleFps = 10 } = {}) {
  check(signal);
  onProgress({ phase: "loading", fraction: 0, time: 0, duration: 0 });
  const models = await initializeModels();
  check(signal);
  // Rebuild the pose graph between files, then use video tracking inside this
  // clip. A new clip must not inherit the previous player's tracking region.
  await models.pose.setOptions({ runningMode: "IMAGE" });
  await models.pose.setOptions({ runningMode: "VIDEO" });
  check(signal);
  const timestampBase = Math.max(performance.now(), lastPoseTimestamp + 100);
  const video = document.createElement("video");
  video.hidden = true;
  video.muted = true;
  video.playsInline = true;
  video.preload = "auto";
  document.body.append(video);
  const url = URL.createObjectURL(file);
  const scope = new AbortController();
  const relay = () => scope.abort();
  signal?.addEventListener("abort", relay, { once: true });
  const scanSignal = scope.signal;
  try {
    const firstFrame = decodedFrame(video, scanSignal);
    const metadata = eventOnce(video, "loadedmetadata", scanSignal);
    // Both promises are observed immediately, including if metadata fails.
    const initial = Promise.all([metadata, firstFrame]);
    video.src = url;
    video.load();
    await initial;
    if (!Number.isFinite(video.duration)) {
      await seekFrame(video, 1e10, scanSignal);
      if (!Number.isFinite(video.duration) || video.duration <= 0) throw new Error("无法确定视频时长，暂时不能自动分析此文件。");
      await seekFrame(video, 0, scanSignal);
    }
    if (video.duration <= 0 || !video.videoWidth) throw new Error("视频没有可分析的画面。");
    const duration = video.duration;
    const canvas = document.createElement("canvas");
    const scale = Math.min(1, 1280 / video.videoWidth);
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    const ctx = canvas.getContext("2d", { alpha: false });
    const frames = [];
    const step = 1 / clamp(sampleFps, 1, 10);
    const count = Math.ceil(duration / step) + 1;
    for (let i = 0; i < count; i++) {
      check(scanSignal);
      const time = Math.min(i * step, Math.max(0, duration - 0.001));
      if (frames.length && time <= frames[frames.length - 1].time) break;
      await seekFrame(video, time, scanSignal);
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      const faces = faceBoxes(models.face, canvas);
      lastPoseTimestamp = timestampBase + time * 1000;
      const poses = models.pose.detectForVideo(canvas, lastPoseTimestamp).landmarks;
      const heads = poses.map((pose) => headBoxFromPose(pose, canvas.width, canvas.height)).filter(Boolean);
      frames.push({ time, boxes: mergeBoxes([...faces, ...heads]) });
      onProgress({ phase: "scanning", fraction: (i + 1) / count, time, duration });
      // Inference is synchronous; yield between frames so cancel/UI can run.
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    check(scanSignal);
    const result = buildTimeline(frames, duration);
    if (!result.detectedFrames) throw new Error("没有找到可用的人脸或头部。请改用手动遮盖，或使用人物更清晰的视频。");
    return result;
  } finally {
    scope.abort();
    signal?.removeEventListener("abort", relay);
    video.pause();
    video.removeAttribute("src");
    video.load();
    video.remove();
    URL.revokeObjectURL(url);
  }
}

export async function analyzeVideo(file, options = {}) {
  // A cancelled scan can still be returning from synchronous WASM inference.
  // Serialize access so a new file cannot mutate that scan's model graph.
  const previous = scanQueue;
  let release;
  scanQueue = new Promise((resolve) => { release = resolve; });
  await previous;
  try {
    check(options.signal);
    return await runAnalysis(file, options);
  } finally { release(); }
}
