// Pose sampling runs entirely in the browser with the locally installed model.
// It supplies observations, not coaching judgments or measured 3D biomechanics.
import { normalizeRegion, cropRect, mapLandmark } from './pose_region.js';
export const DEFAULT_MODEL_VARIANT = 'full';
export const MODEL_VERSIONS = Object.freeze({lite: "mediapipe-1.0.1/pose-lite-v1", full: "mediapipe-1.0.1/pose-full-v1"});
export const MODEL_VERSION = MODEL_VERSIONS[DEFAULT_MODEL_VARIANT];
export const MAX_DURATION = 60;
let modelPromise = null;
let model = null;
let loadedVariant = null;
let queue = Promise.resolve();
let lastTimestamp = 0;

const abortError = () => new DOMException("已取消动作分析", "AbortError");
function check(signal) { if (signal?.aborted) throw abortError(); }

async function initialize(variant) {
  if (!Object.hasOwn(MODEL_VERSIONS, variant)) throw new Error("未知的动作模型。");
  if (!modelPromise || loadedVariant !== variant) {
    model?.close();
    model = null;
    loadedVariant = variant;
    modelPromise = (async () => {
      const { FilesetResolver, PoseLandmarker } = await import("./vendor/mediapipe/vision_bundle.mjs");
      const files = await FilesetResolver.forVisionTasks("./vendor/mediapipe/wasm");
      model = await PoseLandmarker.createFromOptions(files, {
        baseOptions: { modelAssetPath: `./vendor/models/pose_landmarker_${variant}.task`, delegate: "CPU" },
        runningMode: "IMAGE", numPoses: 4, minPoseDetectionConfidence: 0.45,
        minPosePresenceConfidence: 0.45, minTrackingConfidence: 0.5, outputSegmentationMasks: false,
      });
      return model;
    })().catch(error => {
      modelPromise = null;
      model = null;
      throw new Error(`动作模型未能加载，请先运行 setup-auto.cmd，再重试。${error.message ? `（${error.message}）` : ""}`);
    });
  }
  return modelPromise;
}

function eventOnce(target, name, signal, timeoutMs = 20000) {
  return new Promise((resolve, reject) => {
    let timer;
    const cleanup = () => {
      clearTimeout(timer);
      target.removeEventListener(name, done);
      target.removeEventListener("error", failed);
      signal?.removeEventListener("abort", aborted);
    };
    const done = () => { cleanup(); resolve(); };
    const failed = () => { cleanup(); reject(new Error("无法解码视频，请使用浏览器支持的 MP4 或 WebM。")); };
    const aborted = () => { cleanup(); reject(abortError()); };
    if (signal?.aborted) { aborted(); return; }
    target.addEventListener(name, done, { once: true });
    target.addEventListener("error", failed, { once: true });
    signal?.addEventListener("abort", aborted, { once: true });
    timer = setTimeout(() => { cleanup(); reject(new Error("等待视频画面超时，请换用短片重试。")); }, timeoutMs);
  });
}

function presentedFrame(video, signal) {
  if (!video.requestVideoFrameCallback) return Promise.resolve();
  return new Promise((resolve, reject) => {
    let callback, timer;
    const cleanup = () => {
      clearTimeout(timer);
      if (callback !== undefined) video.cancelVideoFrameCallback(callback);
      signal?.removeEventListener("abort", aborted);
    };
    const aborted = () => { cleanup(); reject(abortError()); };
    if (signal?.aborted) { aborted(); return; }
    signal?.addEventListener("abort", aborted, { once: true });
    callback = video.requestVideoFrameCallback(() => { cleanup(); resolve(); });
    timer = setTimeout(() => { cleanup(); reject(new Error("无法读取分析帧，请重试或缩短视频。")); }, 20000);
  });
}

async function seek(video, time, signal) {
  check(signal);
  if (Math.abs(video.currentTime - time) < 1e-5 && !video.seeking && video.readyState >= 2) return;
  const promises = Promise.all([eventOnce(video, "seeked", signal), presentedFrame(video, signal)]);
  video.currentTime = time;
  await promises;
  check(signal);
}

async function sample(file, { signal, onProgress = () => {}, sampleFps = 12, modelVariant = DEFAULT_MODEL_VARIANT, region = null } = {}) {
  check(signal);
  const selectedRegion = normalizeRegion(region);
  if (!Object.hasOwn(MODEL_VERSIONS, modelVariant)) throw new Error("未知的动作模型。");
  if (!(file instanceof Blob) || !file.size) throw new Error("请选择有效的视频文件。");
  if (file.size > 1024 * 1024 * 1024) throw new Error("视频过大，请先剪成 10–20 秒的短片。");
  if (!Number.isFinite(sampleFps) || sampleFps < 1 || sampleFps > 15) throw new Error("分析采样率应在 1–15 帧/秒之间。");
  const scope = new AbortController();
  const relay = () => scope.abort();
  signal?.addEventListener("abort", relay, { once: true });
  const scanSignal = scope.signal;
  const video = document.createElement("video");
  video.hidden = true;
  video.muted = true;
  video.playsInline = true;
  video.preload = "auto";
  document.body.append(video);
  const url = URL.createObjectURL(file);
  try {
    onProgress({ phase: "loading", fraction: 0, time: 0, duration: 0 });
    const ready = Promise.all([eventOnce(video, "loadedmetadata", scanSignal), presentedFrame(video, scanSignal)]);
    video.src = url;
    video.load();
    await ready;
    check(scanSignal);
    // Some recorder WebMs only expose duration after seeking to their end.
    if (!Number.isFinite(video.duration)) {
      await seek(video, 1e10, scanSignal);
      await seek(video, 0, scanSignal);
    }
    const duration = video.duration;
    if (!Number.isFinite(duration) || duration <= 0 || !video.videoWidth) throw new Error("无法确定视频时长或画面尺寸。");
    if (duration > MAX_DURATION + 0.1) throw new Error("这一版每次分析最长 60 秒，请先剪成 10–20 秒短片。");
    const crop = cropRect(selectedRegion, video.videoWidth, video.videoHeight);
    const pose = await initialize(modelVariant);
    check(scanSignal);
    // Rebuild tracking state between files. Access is serialized by scanPoses.
    await pose.setOptions({ runningMode: "IMAGE" });
    await pose.setOptions({ runningMode: "VIDEO" });
    check(scanSignal);
    const timestampBase = Math.max(performance.now(), lastTimestamp + 1000);
    const canvas = document.createElement("canvas");
    const scale = Math.min(1, 1280 / Math.max(crop.sw, crop.sh));
    canvas.width = Math.max(1, Math.round(crop.sw * scale));
    canvas.height = Math.max(1, Math.round(crop.sh * scale));
    const context = canvas.getContext("2d", { alpha: false });
    const frames = [];
    const count = Math.ceil(duration * sampleFps) + 1;
    for (let index = 0; index < count; index++) {
      check(scanSignal);
      const time = Math.min(index / sampleFps, Math.max(0, duration - 0.001));
      if (frames.length && time <= frames[frames.length - 1].time) break;
      await seek(video, time, scanSignal);
      context.drawImage(video, crop.sx, crop.sy, crop.sw, crop.sh, 0, 0, canvas.width, canvas.height);
      lastTimestamp = timestampBase + time * 1000;
      const result = pose.detectForVideo(canvas, lastTimestamp);
      const poses = result.landmarks.map(points => points.map(point => mapLandmark({
        x: point.x, y: point.y, z: point.z,
        // An extrapolated joint outside the chosen image region is not visible
        // evidence, even if its mapped coordinate falls inside the full video.
        visibility: point.x >= 0 && point.x <= 1 && point.y >= 0 && point.y <= 1 ? point.visibility ?? 0 : 0,
        presence: point.presence ?? null,
      }, crop, video.videoWidth, video.videoHeight)));
      frames.push({ time, poses });
      result.close?.();
      onProgress({ phase: "scanning", fraction: (index + 1) / count, time, duration });
      // WASM inference is synchronous. Yield so cancel and playback remain usable.
      await new Promise(resolve => setTimeout(resolve, 0));
    }
    check(scanSignal);
    return { frames, duration, width: video.videoWidth, height: video.videoHeight,
      sampleFps, modelVersion: MODEL_VERSIONS[modelVariant], sampledWidth: canvas.width, sampledHeight: canvas.height,
      samplingRegion: selectedRegion ? {pixelRect: crop, sourceWidth: video.videoWidth, sourceHeight: video.videoHeight} : null };
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

export async function scanPoses(file, options = {}) {
  const previous = queue;
  let release;
  queue = new Promise(resolve => { release = resolve; });
  await previous;
  try { check(options.signal); return await sample(file, options); }
  finally { release(); }
}

export async function disposePoseModel() {
  // Join the same queue so a later scan cannot overtake disposal.
  const previous = queue;
  let release;
  queue = new Promise(resolve => { release = resolve; });
  await previous;
  try {
    model?.close();
    model = null;
    modelPromise = null;
    loadedVariant = null;
  } finally { release(); }
}
