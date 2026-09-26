# Third-party components

The tennis-coach source code uses the MIT License. Downloaded dependencies and
models retain their own licenses. No user video is included in these assets.

## MediaPipe Tasks Vision 1.0.1

- Project: <https://github.com/google-ai-edge/mediapipe>
- Distribution: `@mediapipe/tasks-vision` version **1.0.1**, from the official npm
  registry. Its `package.json` declares **Apache-2.0**.
- The npm archive is pinned by SHA-512 SRI; every installed file is additionally
  pinned by SHA-256 in `scripts/auto_assets.json`.
- Installed files: `web/vendor/mediapipe/vision_bundle.mjs`, `package.json`, and
  the package's JavaScript/WASM runtime variants under `wasm/`.
- This particular npm archive contains no standalone LICENSE or NOTICE file.
  The installer therefore preserves the upstream Apache License at
  `web/vendor/mediapipe/LICENSE`, copied without modification from repository
  commit `6756ba4d59bdd8ac44173df8d4ab4069c0a747ec` and pinned by SHA-256.
  This commit identifies the license source, not the npm release's source commit.
  Copyright/license notices embedded in distributed runtime files are retained.

## Model weights

Both model cards explicitly specify **Apache License, Version 2.0**. These model
licenses are verified separately from the JavaScript package license.

| Model | Official model version | Installed file |
| --- | --- | --- |
| BlazeFace full-range | `float16/1` | `web/vendor/models/blaze_face_full_range.tflite` |
| Pose Landmarker lite | `float16/1` | `web/vendor/models/pose_landmarker_lite.task` |

The exact Google-hosted download URLs and SHA-256 hashes are in
`scripts/auto_assets.json`. The installer also saves the corresponding official
model cards next to the weights:

- [BlazeFace full-range model card](https://storage.googleapis.com/mediapipe-assets/MediaPipe%20BlazeFace%20Model%20Card%20%28Full%20Range%29.pdf)
- [BlazePose GHUM model card](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20BlazePose%20GHUM%203D.pdf)

The full-range face model is sensitive to face size, orientation, motion blur,
and lighting. Its model card excludes back-of-head detection and people too far
away (for example, more than five metres). The older pose model card also
describes distance/head-visibility limitations. Current Tasks APIs can request
multiple poses; that does not establish complete coverage of every person in a
tennis video. A pose-derived head box is an estimate, not an identity check or a
guarantee that all facial pixels have been found. Automatic output needs review.

Human segmentation alone does not preserve tennis balls, rackets, or ball paths;
these models are not a complete automatic background privacy system.

## Local installation and redistribution

`setup-auto.cmd` / `python scripts/setup_auto.py` download only the pinned code,
model, license, and model-card files from official HTTPS endpoints. The installer
does not read videos and has no video-upload operation. Once installed, matching
assets are reused without network access. `python scripts/setup_auto.py --check`
performs an offline integrity check. Inference assets are served by the local
app; they are excluded from the Git repository.

If redistributing a bundle that includes these dependencies or models, preserve
their license texts and attribution rather than relabeling them as MIT.

## Optional dataset tools

`requirements-data.txt` separately pins `yt-dlp[default,curl-cffi]` 2026.8.19,
`curl-cffi` 0.16.3,
`deno` 2.9.7, and `imageio-ffmpeg` 0.6.0. They are installed in the ignored
project `.venv`; their executables and dependencies are not committed here.

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) handles individual video URLs.
  Its source is under the Unlicense; bundled and installed dependencies retain
  their own licenses. The default extra includes the EJS support package.
- [curl_cffi](https://github.com/lexiforest/curl_cffi) supplies browser-compatible
  HTTP requests required by some public video extractors, including Dailymotion.
  Its Python binding is MIT-licensed; bundled libraries retain their notices.
  It does not supply login cookies or account credentials.
- [Deno](https://github.com/denoland/deno) provides the JavaScript runtime used
  by yt-dlp. The installer preserves the Python distribution's packaged notices.
- [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) supplies the Python
  wrapper and a platform-specific FFmpeg executable. FFmpeg licensing depends
  on the build and enabled components; consult the installed executable's
  `-L` output and [FFmpeg's licensing information](https://ffmpeg.org/legal.html)
  before redistributing its binaries.

Media-source permissions are recorded separately from tool licenses. A
successful download does not establish permission to republish that footage.
