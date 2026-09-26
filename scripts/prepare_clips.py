"""Import local originals and prepare traceable, unlabelled tennis clips."""

from __future__ import annotations

import argparse
import copy
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "local"
RAW = LOCAL / "videos" / "raw"
CLIPS = LOCAL / "videos" / "clips"
DATASET = LOCAL / "dataset"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".ogv"}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def private_path(path):
    path = path.resolve()
    if not path.is_relative_to(LOCAL.resolve()):
        raise ValueError(f"Output must stay inside {LOCAL}")
    return path


def relative(path):
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def atomic_text(path, content, encoding="utf-8"):
    path = private_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding=encoding)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def media_tools():
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise ValueError("Run setup-data.cmd first (imageio-ffmpeg is required).") from exc
    return imageio_ffmpeg


def probe(path):
    reader = media_tools().read_frames(str(path))
    try:
        info = next(reader)
    finally:
        reader.close()
    duration = float(info.get("duration") or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"Cannot read a finite duration: {path}. Remux duration-less recordings first.")
    return {"duration": duration, "fps": info.get("fps"),
            "width": info["size"][0], "height": info["size"][1]}


def plan_windows(start, end, target=15, minimum=10, maximum=20):
    values = (start, end, target, minimum, maximum)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Times must be finite numbers.")
    if start < 0 or end <= start or not 0 < minimum <= target <= maximum:
        raise ValueError("Require 0 <= start < end and 0 < min <= target <= max.")
    duration = end - start
    fewest = max(1, math.ceil(duration / maximum - 1e-9))
    most = math.floor(duration / minimum + 1e-9)
    if most < fewest:
        if duration < minimum:
            return [(round(start, 3), round(end, 3))]
        raise ValueError("This time window cannot be split within the requested duration limits.")
    count = max(fewest, min(most, math.floor(duration / target + 0.5)))
    edges = [round(start + duration * i / count, 3) for i in range(count + 1)]
    return list(zip(edges, edges[1:]))


def collect_inputs(inputs):
    found = []
    for value in inputs:
        path = Path(value).expanduser().resolve()
        if not path.exists():
            raise ValueError(f"Input not found: {path}")
        if path.is_dir():
            if path != RAW.resolve() and not path.is_relative_to(RAW.resolve()):
                raise ValueError("Directory scanning is limited to local/videos/raw; pass other files individually.")
            matches = []
            for item in sorted(path.rglob("*")):
                if not item.is_file() or item.suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
                sidecar = item.parent / "source.json"
                if sidecar.exists():
                    registered = json.loads(sidecar.read_text(encoding="utf-8"))
                    if registered.get("file") != item.name:
                        # Failed downloads may leave individual streams. Those
                        # are not completed originals and must not enter clips.
                        continue
                matches.append(item)
        else:
            matches = [path]
        for item in matches:
            if item.suffix.lower() not in VIDEO_EXTENSIONS:
                raise ValueError(f"Unsupported video extension: {item.name}")
            if item.is_relative_to(CLIPS.resolve()) or item.is_relative_to((LOCAL / "videos" / "redacted").resolve()):
                raise ValueError("Derived clips must not be imported as new originals.")
            if item not in found:
                found.append(item)
    if not found:
        raise ValueError("No raw videos found. Download videos or drag one onto prepare-clips.cmd first.")
    return found


def import_raw(path):
    fingerprint = digest_file(path)
    if path.is_relative_to(RAW.resolve()) and (path.parent / "source.json").exists():
        raw = path
    else:
        directory = private_path(RAW / "local" / fingerprint[:16])
        directory.mkdir(parents=True, exist_ok=True)
        raw = directory / ("media" + path.suffix.lower())
        if raw.exists():
            if digest_file(raw) != fingerprint:
                raise ValueError(f"Existing raw copy does not match input: {raw}")
        else:
            temporary = raw.with_name(raw.name + f".{uuid.uuid4().hex}.tmp")
            try:
                shutil.copy2(path, temporary)
                temporary.replace(raw)
            finally:
                temporary.unlink(missing_ok=True)
    sidecar = raw.parent / "source.json"
    if sidecar.exists():
        source = json.loads(sidecar.read_text(encoding="utf-8"))
        if source.get("file") != raw.name:
            raise ValueError(f"source.json points to a different file: {sidecar}")
        if source.get("sha256") and source["sha256"] != fingerprint:
            raise ValueError(f"Raw video changed since registration: {raw}")
    else:
        source = {"schema_version": "0.1", "source_id": f"local:{fingerprint[:16]}",
                  "platform": "local", "url": None, "title": path.stem, "uploader": None,
                  "license": None, "rights_status": "unknown", "file": raw.name,
                  "original_playback_rate": "unknown", "downloaded_at": None,
                  "imported_at": utc_now(), "original_filename": path.name}
    source.setdefault("source_group_id", source["source_id"])
    source["sha256"] = fingerprint
    info = probe(raw)
    source.update(info)
    write_json(sidecar, source)
    return raw, source


def encode_clip(raw, output, start, end):
    output = private_path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.stem + "." + uuid.uuid4().hex + ".tmp.mp4")
    command = [media_tools().get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
               "-ss", f"{start:.3f}", "-i", str(raw), "-t", f"{end - start:.3f}",
               "-map", "0:v:0", "-map", "0:a?", "-map_metadata", "-1",
               "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-preset", "veryfast",
               "-crf", "18", "-fps_mode", "passthrough", "-c:a", "aac", "-b:a", "128k",
               "-movflags", "+faststart", str(temporary)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        if result.returncode:
            raise ValueError(f"FFmpeg failed: {result.stderr[-1500:]}")
        info = probe(temporary)
        if abs(info["duration"] - (end - start)) > max(0.25, 2 / (info.get("fps") or 30)):
            raise ValueError("Encoded duration differs from the requested window; output was not accepted.")
        if output.exists():
            raise ValueError(f"Refusing to replace an existing clip: {output}")
        temporary.replace(output)
        return info
    finally:
        temporary.unlink(missing_ok=True)


def make_label(record, source):
    label = copy.deepcopy(json.loads((ROOT / "templates" / "clip-label.json").read_text(encoding="utf-8")))
    for key in ("source_id", "parent_raw_path", "clip_id", "clip_path", "start_seconds", "end_seconds", "source_group_id"):
        label[key] = record[key]
    label["source"].update({"platform": source["platform"], "url": source.get("url"),
                            "creator_display_name": source.get("uploader"),
                            "acquired_on": source.get("downloaded_at") or source.get("imported_at"),
                            "license_or_permission": source.get("license") or "unknown"})
    return label


def prepare(raw, source, windows, minimum, maximum):
    source_key = re.sub(r"[^A-Za-z0-9_-]", "_", source["source_id"])[:64]
    count = 0
    for start, end in windows:
        clip_id = f"{source_key}_{round(start * 1000):07d}-{round(end * 1000):07d}_{source['sha256'][:8]}"
        output = private_path(CLIPS / source_key / (clip_id + ".mp4"))
        item_path = private_path(DATASET / "items" / (clip_id + ".json"))
        label_path = private_path(DATASET / "labels" / (clip_id + ".json"))
        if output.exists():
            if not item_path.exists():
                raise ValueError(f"Clip exists without provenance record: {output}")
            record = json.loads(item_path.read_text(encoding="utf-8"))
            if digest_file(output) != record.get("clip_sha256"):
                raise ValueError(f"Clip changed since registration: {output}")
            print(f"Existing: {relative(output)}", flush=True)
        else:
            info = encode_clip(raw, output, start, end)
            record = {"schema_version": "0.1", "clip_id": clip_id, "source_id": source["source_id"],
                      "source_group_id": source["source_group_id"], "parent_raw_path": relative(raw),
                      "parent_sha256": source["sha256"], "clip_path": relative(output),
                      "clip_sha256": digest_file(output), "label_path": relative(label_path),
                      "source_url": source.get("url"), "start_seconds": start, "end_seconds": end,
                      "requested_duration": round(end - start, 3), "encoded_duration": info["duration"],
                      "encoded_fps": info["fps"], "outside_duration_target": not minimum <= end - start <= maximum,
                      "boundary_review": "pending", "audio": "preserved_if_present", "created_at": utc_now()}
            write_json(item_path, record)
            print(f"Created: {relative(output)} ({info['duration']:.2f}s)", flush=True)
            count += 1
        if not label_path.exists():
            write_json(label_path, make_label(record, source))
    return count


def rebuild_index():
    fields = ["clip_id", "source_id", "source_group_id", "parent_raw_path", "clip_path", "label_path",
              "start_seconds", "end_seconds", "encoded_duration", "source_url", "review_status", "assistant_review_status", "boundary_review"]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for item in sorted((DATASET / "items").glob("*.json")):
        record = json.loads(item.read_text(encoding="utf-8"))
        label = json.loads((ROOT / record["label_path"]).read_text(encoding="utf-8"))
        record["review_status"] = label["review"]["status"]
        record["assistant_review_status"] = (label.get("assistant_review") or {}).get("status", "")
        record["boundary_review"] = label["boundary_review"]
        writer.writerow(record)
    atomic_text(DATASET / "clips.csv", buffer.getvalue(), "utf-8-sig")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", help="A raw video file or directory inside local/videos/raw; repeatable")
    parser.add_argument("--target-seconds", type=float, default=15)
    parser.add_argument("--min-seconds", type=float, default=10)
    parser.add_argument("--max-seconds", type=float, default=20)
    parser.add_argument("--start", type=float, default=0, help="Original-video start time (one input only)")
    parser.add_argument("--end", type=float, help="Original-video end time (one input only)")
    args = parser.parse_args()
    for path in (RAW, CLIPS, LOCAL / "videos" / "redacted", DATASET / "items", DATASET / "labels"):
        private_path(path).mkdir(parents=True, exist_ok=True)
    inputs = collect_inputs(args.input or [str(RAW)])
    if len(inputs) != 1 and (args.start or args.end is not None):
        parser.error("--start/--end require exactly one input video")
    failures = []
    created = 0
    seen = set()
    for path in inputs:
        try:
            raw, source = import_raw(path)
            identity = (source["source_id"], source["sha256"])
            if identity in seen:
                continue
            seen.add(identity)
            end = source["duration"] if args.end is None else args.end
            if end > source["duration"] + 0.001:
                raise ValueError("Requested end is beyond the original duration.")
            windows = plan_windows(args.start, end, args.target_seconds, args.min_seconds, args.max_seconds)
            created += prepare(raw, source, windows, args.min_seconds, args.max_seconds)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
            failures.append({"input": str(path), "error": str(error)})
            print(f"Failed: {path.name}: {error}", file=sys.stderr)
    rebuild_index()
    report = {"created_at": utc_now(), "new_clips": created, "inputs": len(inputs), "failures": failures}
    write_json(DATASET / "runs" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json"), report)
    print(f"New clips: {created}. Failed inputs: {len(failures)}. Index: {DATASET / 'clips.csv'}")
    print("All new labels are unreviewed. Check stroke boundaries before judging technique.")
    return int(bool(failures))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Cannot prepare clips: {error}", file=sys.stderr)
        sys.exit(1)
