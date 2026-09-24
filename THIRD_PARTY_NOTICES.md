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
