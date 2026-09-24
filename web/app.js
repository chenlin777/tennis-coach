(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const video = $("sourceVideo");
  const canvas = $("videoCanvas");
  const overlay = $("overlayCanvas");
  const context = canvas.getContext("2d", { alpha: false });
  const overlayContext = overlay.getContext("2d");
  const tinyCanvas = document.createElement("canvas");
  const tinyContext = tinyCanvas.getContext("2d", { alpha: false });
  const state = {
    ready: false, sourceUrl: null, downloadUrl: null, file: null,
    faceMode: "auto", backgroundMode: "none", faces: [], keep: null,
    drawing: null, start: null, draft: null, pointerId: null,
    animation: null, session: null, loadId: 0, durationProbe: null,
    framePreparation: null, decodedFrameReady: false,
    scanSession: null, scanId: 0, analysis: null, analysisLoadId: null,
    autoModule: null, autoModulePromise: null,
  };

  function clamp(value, min = 0, max = 1) {
    return Math.min(max, Math.max(min, value));
  }

  function rectangleBetween(a, b) {
    const x1 = clamp(a.x), y1 = clamp(a.y);
    const x2 = clamp(b.x), y2 = clamp(b.y);
    return { x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1), h: Math.abs(y2 - y1) };
  }

  function pointInCanvas(clientX, clientY, bounds) {
    return { x: clamp((clientX - bounds.left) / bounds.width), y: clamp((clientY - bounds.top) / bounds.height) };
  }

  // Pure geometry is exposed for local browser checks; no video data is exposed.
  window.TennisPrivacyGeometry = Object.freeze({ clamp, rectangleBetween, pointInCanvas });

  function formatTime(seconds) {
    if (!Number.isFinite(seconds)) return "00:00";
    const total = Math.max(0, Math.floor(seconds));
    return `${Math.floor(total / 60).toString().padStart(2, "0")}:${(total % 60).toString().padStart(2, "0")}`;
  }

  function setStatus(message, error = false) {
    $("status").textContent = message;
    $("status").classList.toggle("error", error);
  }

  function exportMimeType() {
    if (!window.MediaRecorder || !canvas.captureStream) return null;
    return ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"].find((type) => MediaRecorder.isTypeSupported(type)) || null;
  }

  function currentAnalysis() {
    return state.analysisLoadId === state.loadId ? state.analysis : null;
  }

  function autoExportReady() {
    const analysis = currentAnalysis();
    return Boolean(analysis && (!analysis.reviewIntervals.length || $("reviewConfirm").checked));
  }

  function automaticMasks(analysis, time) {
    return analysis && state.autoModule ? state.autoModule.getMasksAt(analysis, time) : [];
  }

  function updateControls() {
    const exporting = Boolean(state.session);
    const scanning = Boolean(state.scanSession);
    const busy = exporting || scanning;
    const available = state.ready && !busy;
    $("videoFile").disabled = exporting;
    document.querySelector(".file-button").classList.toggle("disabled", exporting);
    ["playPause", "timeline", "playbackRate", "showOutlines", "faceOptions", "backgroundOptions"].forEach((id) => { $(id).disabled = !available; });
    $("drawFace").disabled = !available || state.faceMode === "none";
    $("drawFace").textContent = state.faceMode === "auto" ? "补画固定遮盖区" : "画遮盖区";
    $("clearFaces").disabled = !available || !state.faces.length;
    $("drawKeep").disabled = !available || state.backgroundMode !== "keep";
    $("clearKeep").disabled = !available || !state.keep;
    $("exportVideo").disabled = !available || !exportMimeType() || (state.faceMode === "auto" && !autoExportReady());
    $("cancelExport").hidden = !exporting;
    $("exportProgress").hidden = !exporting;
    $("downloadVideo").hidden = busy || !state.downloadUrl;
    $("playPause").textContent = video.paused ? "播放" : "暂停";
    $("faceCount").textContent = state.faces.length ? `已画 ${state.faces.length} 个固定遮盖区` : (state.faceMode === "auto" ? "可选：补画自动漏掉的区域" : "尚未画遮盖区");
    $("keepStatus").textContent = state.keep ? "已画 1 个固定保留区" : "尚未画保留区";
    $("drawFace").setAttribute("aria-pressed", String(state.drawing === "face"));
    $("drawKeep").setAttribute("aria-pressed", String(state.drawing === "keep"));
    $("autoPanel").hidden = state.faceMode !== "auto";
    $("scanVideo").disabled = !available || state.faceMode !== "auto";
    $("scanVideo").textContent = currentAnalysis() ? "重新分析" : "一键分析并跟随遮盖";
    $("cancelScan").hidden = !scanning;
    $("analysisProgress").hidden = !scanning;
    $("reviewConfirm").disabled = !available || !currentAnalysis();
    $("reviewList").querySelectorAll("button").forEach((button) => { button.disabled = !available; });
    overlay.hidden = busy || !$("showOutlines").checked;
  }

  function updateTime() {
    if (!state.ready) return;
    $("timeline").value = String(video.currentTime || 0);
    $("timeDisplay").textContent = `${formatTime(video.currentTime)} / ${formatTime(video.duration)}`;
    if (state.session && Number.isFinite(video.duration)) {
      $("exportProgress").value = clamp(video.currentTime / video.duration) * 100;
    }
  }

  function pixels(rect) {
    return [rect.x * canvas.width, rect.y * canvas.height, rect.w * canvas.width, rect.h * canvas.height];
  }

  function drawFrame() {
    if (!state.ready || (video.readyState < 2 && !state.decodedFrameReady)) return;
    const width = canvas.width, height = canvas.height;
    const filters = state.session ? state.session.filters : state;
    context.imageSmoothingEnabled = true;
    context.drawImage(video, 0, 0, width, height);
    if (filters.backgroundMode === "keep" && filters.keep) {
      tinyContext.drawImage(video, 0, 0, tinyCanvas.width, tinyCanvas.height);
      context.imageSmoothingEnabled = false;
      context.drawImage(tinyCanvas, 0, 0, tinyCanvas.width, tinyCanvas.height, 0, 0, width, height);
      context.imageSmoothingEnabled = true;
      context.save();
      context.beginPath();
      context.rect(...pixels(filters.keep));
      context.clip();
      context.drawImage(video, 0, 0, width, height);
      context.restore();
    }
    if (filters.faceMode !== "none") {
      context.fillStyle = "#263832";
      const movingMasks = filters.faceMode === "auto" ? automaticMasks(state.session ? filters.analysis : currentAnalysis(), video.currentTime) : [];
      [...movingMasks, ...filters.faces].forEach((rect) => {
        // Round outward so the selected region has no anti-aliased sliver.
        const [x, y, w, h] = pixels(rect);
        context.fillRect(Math.floor(x), Math.floor(y), Math.ceil(x + w) - Math.floor(x), Math.ceil(y + h) - Math.floor(y));
      });
    }
    drawOverlay();
  }

  function drawOverlay() {
    overlayContext.clearRect(0, 0, overlay.width, overlay.height);
    if (!state.ready || state.session || state.scanSession) return;
    overlayContext.lineWidth = Math.max(2, canvas.width / 400);
    overlayContext.setLineDash([canvas.width / 100, canvas.width / 150]);
    function outline(rect, color) {
      overlayContext.strokeStyle = color;
      overlayContext.strokeRect(...pixels(rect));
    }
    if (state.faceMode === "auto") automaticMasks(currentAnalysis(), video.currentTime).forEach((rect) => outline(rect, "#83c8ff"));
    if (state.faceMode !== "none") state.faces.forEach((rect) => outline(rect, "#ffdc78"));
    if (state.backgroundMode === "keep" && state.keep) outline(state.keep, "#70efc5");
    if (state.draft) outline(state.draft, "#ffffff");
  }

  function renderLoop() {
    state.animation = null;
    drawFrame();
    updateTime();
    if (!video.paused && !video.ended && state.ready) state.animation = requestAnimationFrame(renderLoop);
  }

  function startRendering() {
    if (state.animation === null) state.animation = requestAnimationFrame(renderLoop);
  }

  function clearDownload() {
    if (state.downloadUrl) URL.revokeObjectURL(state.downloadUrl);
    state.downloadUrl = null;
    $("downloadVideo").removeAttribute("href");
    $("downloadVideo").hidden = true;
  }

  function endDrawing() {
    if (state.pointerId !== null && overlay.hasPointerCapture(state.pointerId)) overlay.releasePointerCapture(state.pointerId);
    state.drawing = null;
    state.start = null;
    state.draft = null;
    state.pointerId = null;
    $("stage").classList.remove("drawing");
    $("drawingHint").textContent = state.faceMode === "auto" ? "自动框会随视频移动；手动画的补充框固定在画面上。" : "手动画的选区固定在画面上，不会自动追踪移动的人。";
    $("drawingHint").classList.remove("active");
    updateControls();
    drawOverlay();
  }

  function beginDrawing(kind) {
    if (!state.ready || state.session || state.scanSession) return;
    video.pause();
    endDrawing();
    state.drawing = kind;
    $("showOutlines").checked = true;
    $("stage").classList.add("drawing");
    $("drawingHint").classList.add("active");
    $("drawingHint").textContent = kind === "face" ? "在画面上拖动，框住要遮盖的范围。可重复画多个区域；按 Esc 退出画框。" : "在画面上拖动，框住要保留的全身、球拍和球路。按 Esc 退出画框。";
    updateControls();
  }

  function setAnalysisStatus(message, error = false) {
    $("analysisStatus").textContent = message;
    $("analysisStatus").classList.toggle("error", error);
  }

  function preciseTime(seconds) {
    const tenths = Math.max(0, Math.round(seconds * 10));
    return `${formatTime(Math.floor(tenths / 10))}.${tenths % 10}`;
  }

  function renderAnalysisReview() {
    const analysis = currentAnalysis();
    $("reviewList").replaceChildren();
    $("reviewPanel").hidden = !analysis || !analysis.reviewIntervals.length;
    $("analysisSummary").hidden = !analysis;
    if (!analysis) return;
    $("analysisSummary").textContent = `已扫描 ${analysis.totalFrames} 帧，其中 ${analysis.detectedFrames} 帧检测到脸部或头部。这不是“不会漏遮”的保证。`;
    const loadId = state.loadId;
    analysis.reviewIntervals.forEach((interval) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = `${preciseTime(interval.start)} – ${preciseTime(interval.end)}`;
      button.addEventListener("click", () => {
        if (!state.ready || state.session || state.scanSession || state.loadId !== loadId || currentAnalysis() !== analysis) return;
        video.pause();
        endDrawing();
        video.currentTime = clamp(interval.start, 0, video.duration);
        setStatus("已跳到需要复核的片段。请播放检查，需要时补画固定遮盖区。");
      });
      const reason = document.createElement("span");
      reason.textContent = interval.reason || "这一段检测不连续，需要检查遮盖范围。";
      item.append(button, reason);
      $("reviewList").append(item);
    });
  }

  function stopScan(message = "分析已取消。") {
    const scan = state.scanSession;
    if (!scan) return;
    state.scanSession = null;
    scan.abort.abort();
    $("reviewConfirm").checked = false;
    const restored = currentAnalysis();
    setAnalysisStatus(restored ? `${message} 已保留上次完整结果，请重新检查需要复核的片段。` : `${message} 尚无完整自动分析结果。`);
    updateControls();
    drawFrame();
  }

  $("cancelScan").addEventListener("click", () => stopScan());
  $("reviewConfirm").addEventListener("change", () => { clearDownload(); updateControls(); });
  $("scanVideo").addEventListener("click", async () => {
    if (!state.ready || state.session || state.scanSession || state.faceMode !== "auto") return;
    endDrawing();
    clearDownload();
    $("reviewConfirm").checked = false;
    const scan = { id: ++state.scanId, loadId: state.loadId, abort: new AbortController(), file: state.file };
    state.scanSession = scan;
    const isCurrent = () => state.scanSession === scan && state.loadId === scan.loadId && !scan.abort.signal.aborted;
    video.pause();
    $("analysisProgress").value = 0;
    updateControls();
    setAnalysisStatus("正在本机加载自动分析模型…");
    setStatus("正在分析脸部和头部的位置。视频留在本机；可以取消分析或选择其它文件。");
    try {
      if (!state.autoModulePromise) {
        state.autoModulePromise = import("./auto_privacy.js").catch((error) => {
          state.autoModulePromise = null;
          throw error;
        });
      }
      const module = await state.autoModulePromise;
      if (!isCurrent()) return;
      state.autoModule = module;
      const analysis = await module.analyzeVideo(scan.file, {
        signal: scan.abort.signal,
        onProgress(progress) {
          if (!isCurrent()) return;
          $("analysisProgress").value = Number.isFinite(progress.fraction) ? clamp(progress.fraction) * 100 : 0;
          if (progress.phase === "loading") setAnalysisStatus("正在本机加载自动分析模型…");
          else setAnalysisStatus(`正在分析 ${formatTime(progress.time)} / ${formatTime(progress.duration)}（${Math.round(clamp(progress.fraction || 0) * 100)}%）`);
        },
      });
      if (!isCurrent()) return;
      if (!analysis || !Array.isArray(analysis.frames) || !analysis.frames.length || !analysis.detectedFrames || !Array.isArray(analysis.reviewIntervals)) {
        throw new Error("未检测到可用的脸部或头部位置。请改用手动画框，或换一段全身和头部更清楚的视频。");
      }
      // Commit only a complete result from the currently selected file.
      state.analysis = analysis;
      state.analysisLoadId = scan.loadId;
      state.scanSession = null;
      $("analysisProgress").value = 100;
      renderAnalysisReview();
      updateControls();
      drawFrame();
      if (analysis.reviewIntervals.length) {
        setAnalysisStatus(`分析已完成，有 ${analysis.reviewIntervals.length} 段需要复核。请逐段检查后勾选确认。`);
        setStatus("自动遮盖已可预览。请检查右侧列出的片段，必要时补画，再确认导出。");
      } else {
        setAnalysisStatus("分析已完成，自动遮盖会随视频移动。请完整播放检查后再导出。");
        setStatus("自动分析已完成。请播放检查跟随效果；需要时可以补画固定遮盖区。");
      }
    } catch (error) {
      if (!isCurrent()) return;
      state.scanSession = null;
      const message = error && error.message ? error.message : "自动分析未能完成，请重试或改用手动画框。";
      setAnalysisStatus(currentAnalysis() ? `${message} 已保留上次完整结果。` : message, true);
      setStatus("自动分析未完成，没有生成新的分析结果。可以重试或选择手动画框。", true);
      updateControls();
      drawFrame();
    }
  });

  function cancelDurationProbe() {
    if (state.durationProbe) state.durationProbe.abort();
    state.durationProbe = null;
  }

  function cancelFramePreparation() {
    if (state.framePreparation) state.framePreparation.cancel();
    state.framePreparation = null;
  }

  function prepareFirstFrame(loadId) {
    // Subscribe before setting src: some browsers do not submit a paused,
    // hidden video's first frame until a video-frame callback is requested.
    // loadeddata / readyState alone do not guarantee drawImage has that frame.
    let settle;
    let callbackId = null;
    let timeout;
    let finished = false;
    const preparation = {
      abort: new AbortController(),
      usesFrameCallback: typeof video.requestVideoFrameCallback === "function",
      promise: new Promise((resolve) => { settle = resolve; }),
      finish(available) {
        if (finished) return;
        finished = true;
        clearTimeout(timeout);
        if (callbackId !== null) video.cancelVideoFrameCallback(callbackId);
        if (state.loadId === loadId && available) state.decodedFrameReady = true;
        settle(available && state.loadId === loadId);
      },
      cancel() {
        preparation.abort.abort();
        preparation.finish(false);
      },
    };
    timeout = setTimeout(() => preparation.cancel(), 20000);
    if (preparation.usesFrameCallback) {
      callbackId = video.requestVideoFrameCallback(() => preparation.finish(true));
    }
    return preparation;
  }

  function probeDuration(loadId, signal) {
    // MediaRecorder WebM files may omit a duration. Asking the local decoder to
    // seek beyond the end makes Chromium discover the real final timestamp.
    // Never treat the requested far-away seek position as the video duration.
    return new Promise((resolve, reject) => {
      let returningToStart = false;
      let settled = false;
      let timeout;
      const events = ["durationchange", "seeked", "timeupdate", "loadeddata", "canplay"];
      const cleanup = () => {
        clearTimeout(timeout);
        events.forEach((name) => video.removeEventListener(name, check));
        video.removeEventListener("error", failed);
        signal.removeEventListener("abort", aborted);
      };
      const finish = (error) => {
        if (settled) return;
        settled = true;
        cleanup();
        if (error) reject(error);
        else resolve();
      };
      const aborted = () => finish(new Error("duration-probe-cancelled"));
      const failed = () => finish(new Error("duration-probe-failed"));
      const check = () => {
        if (signal.aborted || state.loadId !== loadId) { aborted(); return; }
        if (!Number.isFinite(video.duration) || video.duration <= 0) return;
        if (!returningToStart) {
          returningToStart = true;
          try { video.currentTime = 0; } catch { failed(); return; }
        }
        if (!video.seeking && video.currentTime < 0.05 && video.readyState >= 2) finish();
      };
      if (signal.aborted || state.loadId !== loadId) { aborted(); return; }
      events.forEach((name) => video.addEventListener(name, check));
      video.addEventListener("error", failed);
      signal.addEventListener("abort", aborted, { once: true });
      timeout = setTimeout(() => finish(new Error("duration-probe-timeout")), 20000);
      try {
        video.preload = "auto";
        video.currentTime = 1e10;
        check();
      } catch { failed(); }
    });
  }

  $("videoFile").addEventListener("change", () => {
    if (state.session) return;
    const file = $("videoFile").files[0];
    if (!file) return;
    state.loadId += 1;
    stopScan("已切换视频，旧分析已取消。");
    state.analysis = null;
    state.analysisLoadId = null;
    $("reviewConfirm").checked = false;
    renderAnalysisReview();
    setAnalysisStatus("视频载入后，点击“一键分析并跟随遮盖”。模型和视频均在本机运行。");
    cancelDurationProbe();
    cancelFramePreparation();
    video.pause();
    state.ready = false;
    state.decodedFrameReady = false;
    endDrawing();
    clearDownload();
    if (state.sourceUrl) URL.revokeObjectURL(state.sourceUrl);
    state.file = file;
    state.faces = [];
    state.keep = null;
    state.sourceUrl = URL.createObjectURL(file);
    $("fileName").textContent = file.name;
    $("fileMeta").textContent = "正在读取视频…";
    $("stage").hidden = true;
    $("emptyPreview").hidden = false;
    $("timeline").value = "0";
    $("timeDisplay").textContent = "00:00 / 00:00";
    state.framePreparation = prepareFirstFrame(state.loadId);
    video.src = state.sourceUrl;
    video.preload = "metadata";
    video.muted = true;
    video.load();
    updateControls();
    setStatus("视频只在本机读取，没有上传。正在载入预览…");
  });

  video.addEventListener("loadedmetadata", async () => {
    const loadId = state.loadId;
    if (!video.videoWidth || !video.videoHeight || video.duration === 0) {
      setStatus("无法读取有效的视频长度或尺寸。请换用可播放的普通 MP4 或 WebM 文件。", true);
      $("fileMeta").textContent = "视频信息读取失败";
      cancelFramePreparation();
      return;
    }
    if (!Number.isFinite(video.duration)) {
      cancelDurationProbe();
      const probe = new AbortController();
      state.durationProbe = probe;
      setStatus("此视频没有记录时长，正在本机读取结尾并返回开头；此时可以选择其它视频。");
      $("fileMeta").textContent = "正在读取视频长度…";
      try {
        await probeDuration(loadId, probe.signal);
      } catch (error) {
        if (state.loadId !== loadId || probe.signal.aborted) return;
        setStatus("未能读取这个视频的有效时长，请换用普通 MP4 或其它可播放的视频。没有上传任何内容。", true);
        $("fileMeta").textContent = "视频长度读取失败";
        if (state.durationProbe === probe) state.durationProbe = null;
        cancelFramePreparation();
        return;
      }
      if (state.loadId !== loadId || probe.signal.aborted) return;
      if (state.durationProbe === probe) state.durationProbe = null;
    }
    // The probe is allowed to finish only with a finite, measured duration.
    if (!Number.isFinite(video.duration) || video.duration <= 0) return;
    const preparation = state.framePreparation;
    if (!preparation) return;
    if (!preparation.usesFrameCallback) {
      // Older browsers can prime the decoder with a tiny seek and return to
      // exactly zero. No content is played or omitted from a later export.
      try {
        await waitForSeek(preparation, Math.min(0.04, video.duration / 2));
        await waitForSeek(preparation, 0);
        preparation.finish(true);
      } catch { preparation.finish(false); }
    }
    const frameAvailable = await preparation.promise;
    if (state.loadId !== loadId) return;
    if (!frameAvailable) {
      $("fileMeta").textContent = "首帧读取失败";
      setStatus("未能读取视频首帧，请重新选择文件或换用近期版本的 Edge / Chrome。", true);
      return;
    }
    canvas.width = overlay.width = video.videoWidth;
    canvas.height = overlay.height = video.videoHeight;
    tinyCanvas.width = Math.max(1, Math.ceil(canvas.width / 40));
    tinyCanvas.height = Math.max(1, Math.ceil(canvas.height / 40));
    state.ready = true;
    video.playbackRate = Number($("playbackRate").value);
    $("timeline").max = String(video.duration);
    $("fileMeta").textContent = `${video.videoWidth} × ${video.videoHeight} · ${formatTime(video.duration)} · ${(state.file.size / 1024 / 1024).toFixed(1)} MB`;
    $("stage").hidden = false;
    $("emptyPreview").hidden = true;
    updateControls();
    updateTime();
    drawFrame();
    if (!exportMimeType()) setStatus("当前浏览器不能导出 Canvas / WebM 视频。请用近期版本的 Edge 或 Chrome 打开此页面；预览仍可使用。", true);
    else setStatus(state.faceMode === "auto" ? "视频已载入。点击“一键分析并跟随遮盖”，完成后播放检查效果。" : "视频已载入。可以不打码，也可以开启处理后画选区。请完整预览后再导出。");
  });

  video.addEventListener("loadeddata", drawFrame);
  video.addEventListener("seeked", () => { drawFrame(); updateTime(); });
  video.addEventListener("timeupdate", updateTime);
  video.addEventListener("play", () => { updateControls(); startRendering(); });
  video.addEventListener("pause", () => { updateControls(); drawFrame(); });
  video.addEventListener("ended", () => {
    drawFrame();
    updateControls();
    const session = state.session;
    if (session && session.recorder && session.recorder.state !== "inactive") session.recorder.stop();
  });
  video.addEventListener("error", () => {
    stopScan("视频读取失败，分析已取消。");
    cancelDurationProbe();
    cancelFramePreparation();
    if (state.session) stopExport("视频播放失败，导出已停止。请换用浏览器支持的视频格式。", true);
    state.ready = false;
    updateControls();
    $("fileMeta").textContent = "浏览器无法播放此文件";
    setStatus("无法播放这个视频。可以尝试 MP4（H.264）或 WebM；文件仍只保留在本机。", true);
  });

  $("playPause").addEventListener("click", async () => {
    if (!state.ready || state.session || state.scanSession) return;
    endDrawing();
    if (!video.paused) video.pause();
    else {
      if (video.ended) video.currentTime = 0;
      try { await video.play(); } catch { setStatus("播放未成功。请确认视频可在此浏览器播放后重试。", true); }
    }
  });
  $("timeline").addEventListener("input", () => {
    if (state.session || state.scanSession) return;
    endDrawing();
    video.currentTime = Number($("timeline").value);
    updateTime();
  });
  $("playbackRate").addEventListener("change", () => { if (!state.session && !state.scanSession) video.playbackRate = Number($("playbackRate").value); });
  $("showOutlines").addEventListener("change", updateControls);
  document.querySelectorAll('input[name="faceMode"]').forEach((input) => input.addEventListener("change", () => {
    if (state.session || state.scanSession) return;
    state.faceMode = input.value;
    endDrawing();
    clearDownload();
    drawFrame();
  }));
  document.querySelectorAll('input[name="backgroundMode"]').forEach((input) => input.addEventListener("change", () => {
    if (state.session || state.scanSession) return;
    state.backgroundMode = input.value;
    endDrawing();
    clearDownload();
    drawFrame();
  }));
  $("drawFace").addEventListener("click", () => beginDrawing("face"));
  $("drawKeep").addEventListener("click", () => beginDrawing("keep"));
  $("clearFaces").addEventListener("click", () => { state.faces = []; $("reviewConfirm").checked = false; endDrawing(); clearDownload(); drawFrame(); });
  $("clearKeep").addEventListener("click", () => { state.keep = null; endDrawing(); clearDownload(); drawFrame(); });

  overlay.addEventListener("pointerdown", (event) => {
    if (!state.drawing || state.session || state.scanSession || event.button !== 0 || state.pointerId !== null) return;
    event.preventDefault();
    state.pointerId = event.pointerId;
    overlay.setPointerCapture(event.pointerId);
    state.start = pointInCanvas(event.clientX, event.clientY, overlay.getBoundingClientRect());
    state.draft = rectangleBetween(state.start, state.start);
  });
  overlay.addEventListener("pointermove", (event) => {
    if (event.pointerId !== state.pointerId || !state.start) return;
    state.draft = rectangleBetween(state.start, pointInCanvas(event.clientX, event.clientY, overlay.getBoundingClientRect()));
    drawOverlay();
  });
  overlay.addEventListener("pointerup", (event) => {
    if (event.pointerId !== state.pointerId || !state.start) return;
    const rect = rectangleBetween(state.start, pointInCanvas(event.clientX, event.clientY, overlay.getBoundingClientRect()));
    const bounds = overlay.getBoundingClientRect();
    if (rect.w * bounds.width < 5 || rect.h * bounds.height < 5) {
      setStatus("选区太小，请重新拖动，画出至少 5 像素宽和高的区域。", true);
    } else {
      if (state.drawing === "face") { state.faces.push(rect); $("reviewConfirm").checked = false; }
      else state.keep = rect;
      clearDownload();
      setStatus("选区已更新。请播放整段视频，检查移动过程中是否仍覆盖需要处理的范围。");
    }
    endDrawing();
    drawFrame();
  });
  overlay.addEventListener("pointercancel", endDrawing);
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && state.drawing) endDrawing(); });

  function waitForSeek(session, time) {
    return new Promise((resolve, reject) => {
      let timeout;
      const cleanup = () => {
        clearTimeout(timeout);
        video.removeEventListener("seeked", onSeek);
        video.removeEventListener("loadeddata", onSeek);
        video.removeEventListener("canplay", onSeek);
        session.abort.signal.removeEventListener("abort", onAbort);
      };
      const onSeek = () => {
        if (video.readyState >= 2 && !video.seeking && Math.abs(video.currentTime - time) < 0.05) {
          cleanup();
          resolve();
        }
      };
      const onAbort = () => { cleanup(); reject(new Error("cancelled")); };
      if (session.abort.signal.aborted) { reject(new Error("cancelled")); return; }
      if (Math.abs(video.currentTime - time) < 0.000001 && video.readyState >= 2 && !video.seeking) { resolve(); return; }
      video.addEventListener("seeked", onSeek);
      video.addEventListener("loadeddata", onSeek);
      video.addEventListener("canplay", onSeek);
      session.abort.signal.addEventListener("abort", onAbort, { once: true });
      timeout = setTimeout(() => { cleanup(); reject(new Error("seek-timeout")); }, 15000);
      video.currentTime = time;
    });
  }

  function releaseSession(session, message, error = false) {
    if (state.session !== session) return;
    session.abort.abort();
    video.pause();
    if (session.stream) session.stream.getTracks().forEach((track) => track.stop());
    state.session = null;
    video.playbackRate = session.previousRate;
    if (state.ready) video.currentTime = clamp(session.previousTime, 0, video.duration);
    updateControls();
    drawFrame();
    setStatus(message, error);
  }

  function stopExport(message = "已取消导出。没有生成新文件，原视频未修改。", error = false) {
    const session = state.session;
    if (!session) return;
    session.cancelled = true;
    session.stopMessage = message;
    session.stopError = error;
    session.abort.abort();
    video.pause();
    if (session.recorder && session.recorder.state !== "inactive") session.recorder.stop();
    else releaseSession(session, message, error);
  }

  $("cancelExport").addEventListener("click", () => stopExport());
  $("exportVideo").addEventListener("click", async () => {
    if (!state.ready || state.session || state.scanSession) return;
    if (state.faceMode === "auto" && !currentAnalysis()) { setStatus("请先完成当前视频的自动分析，或选择手动画框 / 不处理。", true); return; }
    if (state.faceMode === "auto" && !autoExportReady()) { setStatus("请先逐段检查标出的时间段，并勾选复核确认后再导出。", true); return; }
    if (state.faceMode === "manual" && !state.faces.length) { setStatus("已开启脸部遮盖，请先画至少一个遮盖区，或选择“不处理”。", true); return; }
    if (state.backgroundMode === "keep" && !state.keep) { setStatus("已开启环境像素化，请先画保留区，或选择“不处理”。", true); return; }
    const mimeType = exportMimeType();
    if (!mimeType) { setStatus("此浏览器不支持静音 WebM 导出，请使用近期版本的 Edge 或 Chrome。", true); return; }
    endDrawing();
    clearDownload();
    const session = {
      previousTime: video.currentTime, previousRate: video.playbackRate,
      stream: null, recorder: null, chunks: [], cancelled: false, abort: new AbortController(),
      filters: {
        faceMode: state.faceMode, backgroundMode: state.backgroundMode,
        faces: state.faces.map((rect) => ({ ...rect })), keep: state.keep ? { ...state.keep } : null,
        analysis: currentAnalysis(),
      },
    };
    state.session = session;
    video.pause();
    video.playbackRate = 1;
    $("exportProgress").value = 0;
    updateControls();
    setStatus("正在导出静音视频，请保持页面在前台。完成后会出现下载按钮。");
    try {
      await waitForSeek(session, 0);
      if (state.session !== session || session.cancelled) return;
      drawFrame();
      session.stream = canvas.captureStream(30);
      session.recorder = new MediaRecorder(session.stream, { mimeType, videoBitsPerSecond: 6000000 });
      session.recorder.addEventListener("dataavailable", (event) => { if (event.data.size && !session.cancelled) session.chunks.push(event.data); });
      session.recorder.addEventListener("error", () => stopExport("浏览器录制失败，导出已停止。请换一段较短的视频重试。", true));
      session.recorder.addEventListener("stop", () => {
        if (state.session !== session) return;
        if (session.cancelled) { releaseSession(session, session.stopMessage, session.stopError); return; }
        const blob = new Blob(session.chunks, { type: session.recorder.mimeType || mimeType });
        session.chunks = [];
        if (!blob.size) { releaseSession(session, "未生成有效的视频数据，请使用近期版本的 Edge 或 Chrome 重试。", true); return; }
        state.downloadUrl = URL.createObjectURL(blob);
        const download = $("downloadVideo");
        download.href = state.downloadUrl;
        download.download = `${state.file.name.replace(/\.[^.]+$/, "") || "tennis"}-privacy-silent.webm`;
        releaseSession(session, "导出完成。点击“下载处理后的视频”保存静音副本，并检查最终文件；没有上传到任何服务。");
      }, { once: true });
      session.recorder.start(1000);
      await video.play();
      if (state.session === session && !session.cancelled) startRendering();
    } catch (error) {
      if (state.session !== session || session.cancelled) return;
      stopExport(error.message === "seek-timeout" ? "视频未能回到开头，导出已停止。请重新选择视频后再试。" : "导出未能开始。请确认浏览器支持此视频，或换一段较短的视频重试。", true);
    }
  });

  // A background tab can throttle Canvas rendering. Discard that export rather
  // than offering a recording with unprocessed or missing frames.
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && state.session) stopExport("页面进入后台，导出已取消。请保持此页面在前台后重新导出。");
    if (document.hidden && state.scanSession) stopScan("页面进入后台，分析已取消。请保持此页面在前台后重试。");
  });
  window.addEventListener("beforeunload", () => {
    if (state.scanSession) state.scanSession.abort.abort();
    if (state.autoModule) Promise.resolve(state.autoModule.disposeModels()).catch(() => {});
    cancelDurationProbe();
    cancelFramePreparation();
    if (state.session) {
      state.session.cancelled = true;
      state.session.abort.abort();
      if (state.session.stream) state.session.stream.getTracks().forEach((track) => track.stop());
    }
    if (state.sourceUrl) URL.revokeObjectURL(state.sourceUrl);
    if (state.downloadUrl) URL.revokeObjectURL(state.downloadUrl);
  });

  updateControls();
  if (!context || !overlayContext || !tinyContext) setStatus("此浏览器无法使用 Canvas，请用近期版本的 Edge 或 Chrome。", true);
}());
