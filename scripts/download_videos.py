"""Collect public video URLs with yt-dlp and retain local provenance.

No browser cookies, account credentials, playlists, or DRM bypass are enabled.
Downloading is performed only when this CLI is explicitly run without --list-only.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_ROOT = PROJECT_ROOT / "local"
DEFAULT_ROOT = LOCAL_ROOT / "videos" / "raw"


@dataclass(frozen=True)
class DownloadLimits:
    max_duration: float = 600
    max_bytes: int = 500 * 1024 * 1024
    max_height: int = 1080


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_component(value: object) -> str:
    """Keep platform-controlled identifiers out of filesystem syntax."""
    original = str(value or "unknown")
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", original).strip("_") or "unknown"
    reserved = {"CON", "PRN", "AUX", "NUL"} | {
        f"{kind}{number}" for kind in ("COM", "LPT") for number in range(1, 10)
    }
    if safe.upper() in reserved:
        safe = "_" + safe
    if safe != original or len(safe) > 100:
        safe = safe[:80] + "-" + hashlib.sha256(original.encode()).hexdigest()[:12]
    return safe


def local_output_root(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_relative_to(LOCAL_ROOT.resolve()):
        raise ValueError(f"Output must stay inside the ignored local directory: {LOCAL_ROOT}")
    return root


def load_urls(direct: list[str], input_file: Path | None) -> list[str]:
    candidates = list(direct)
    if input_file:
        with input_file.open(encoding="utf-8-sig", newline="") as stream:
            if input_file.suffix.lower() == ".csv":
                reader = csv.DictReader(stream)
                url_column = next(
                    (name for name in reader.fieldnames or [] if name.strip().lower() == "url"), None
                )
                if not url_column:
                    raise ValueError("CSV needs a column named url (case-insensitive).")
                candidates.extend(row.get(url_column, "") for row in reader)
            else:
                candidates.extend(stream.read().splitlines())
    urls: list[str] = []
    for value in candidates:
        url = (value or "").strip()
        if not url or url.startswith("#"):
            continue
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f"Expected a full http(s) video URL: {url}")
        if parsed.username or parsed.password:
            raise ValueError("Do not include account credentials in URLs.")
        if url not in urls:
            urls.append(url)
    return urls


def write_json(path: Path, data: dict) -> None:
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class RunLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def debug(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        self.messages.append("WARNING: " + message)

    def error(self, message: str) -> None:
        self.messages.append("ERROR: " + message)


def ydl_options(ffmpeg: str | None, logger: RunLogger, limits: DownloadLimits = DownloadLimits()) -> dict:
    height = f"[height<=?{limits.max_height}]"
    options = {
        "noplaylist": True,
        "extract_flat": "in_playlist",
        "lazy_playlist": True,
        "playlist_items": "1",
        "socket_timeout": 20,
        "retries": 2,
        "fragment_retries": 2,
        "extractor_retries": 1,
        "file_access_retries": 1,
        "concurrent_fragment_downloads": 1,
        "overwrites": False,
        "continuedl": True,
        "cachedir": False,
        "quiet": True,
        "noprogress": True,
        "color": "no_color",
        "logger": logger,
        # The Python API does not read the user's CLI configuration.
        "cookiefile": None,
        "cookiesfrombrowser": None,
        "usenetrc": False,
        "allow_unplayable_formats": False,
        "remote_components": set(),
        "max_filesize": limits.max_bytes,
        "format": (
            f"bv*{height}[ext=mp4]+ba[ext=m4a]/b{height}[ext=mp4]/bv*{height}+ba/b{height}/bv{height}"
            if ffmpeg else f"b{height}/bv{height}"
        ),
        "merge_output_format": "mp4/mkv",
    }
    if ffmpeg:
        options["ffmpeg_location"] = ffmpeg
    deno = Path(sys.executable).parent / ("deno.exe" if sys.platform == "win32" else "deno")
    if deno.is_file():
        options["js_runtimes"] = {"deno": {"path": str(deno)}}
    return options


def existing_download(root: Path, url: str) -> dict | None:
    """Replay the local archive even if the source website is now unavailable."""
    for sidecar in root.glob("*/*/source.json"):
        directory = sidecar.parent.resolve()
        if not directory.is_relative_to(root):
            continue
        try:
            metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if url not in {metadata.get("url"), metadata.get("requested_url")} or not metadata.get("file"):
            continue
        video = (directory / metadata["file"]).resolve()
        if video.is_relative_to(directory) and video.is_file() and video.stat().st_size > 0:
            return {
                "url": url, "status": "already_downloaded", "messages": [],
                "source_json": str(sidecar.relative_to(PROJECT_ROOT)),
                "file": str(video.relative_to(PROJECT_ROOT)),
            }
    return None


def check_metadata_limits(info: dict, limits: DownloadLimits) -> None:
    duration = info.get("duration")
    if isinstance(duration, (int, float)) and duration > limits.max_duration:
        raise ValueError(f"Duration {duration:g}s exceeds --max-duration {limits.max_duration:g}s.")
    height = info.get("height")
    if isinstance(height, (int, float)) and height > limits.max_height:
        raise ValueError(f"Height {height:g}px exceeds --max-height {limits.max_height}px.")
    formats = info.get("requested_formats") or [info]
    estimated_bytes = sum(item.get("filesize") or item.get("filesize_approx") or 0 for item in formats)
    if estimated_bytes > limits.max_bytes:
        raise ValueError("Selected media exceeds --max-size-mb; increase the limit explicitly if needed.")


class ByteBudget:
    """Count downloaded bytes across the selected video and audio streams."""
    def __init__(self, maximum: int):
        self.maximum = maximum
        self.stream_bytes: dict[str, int] = {}

    def __call__(self, event: dict) -> None:
        key = str(event.get("filename") or event.get("tmpfilename") or "media")
        count = event.get("downloaded_bytes") or 0
        self.stream_bytes[key] = max(self.stream_bytes.get(key, 0), count)
        if sum(self.stream_bytes.values()) > self.maximum:
            raise ValueError("Downloaded media exceeded --max-size-mb; partial files were kept for inspection.")


def provenance(info: dict, url: str, version: str) -> dict:
    platform = str(info.get("extractor") or info.get("extractor_key") or "unknown")
    video_id = str(info.get("id") or hashlib.sha256(url.encode()).hexdigest()[:16])
    # Generic direct-file extractors often use a basename such as "video" as
    # their ID, which is not unique across websites.
    if platform.lower() == "generic":
        canonical_url = info.get("webpage_url") or url
        video_id += "-" + hashlib.sha256(canonical_url.encode()).hexdigest()[:12]
    return {
        "schema_version": 1,
        "source_id": f"{platform}:{video_id}",
        "platform": platform,
        "platform_id": video_id,
        "url": info.get("webpage_url") or url,
        "requested_url": url,
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "license": info.get("license") or None,
        "rights_status": "unknown",
        "file": None,
        "duration": info.get("duration"),
        "fps": info.get("fps"),
        "width": info.get("width"),
        "height": info.get("height"),
        "original_playback_rate": "unknown",
        "downloaded_at": None,
        "collected_at": utc_now(),
        "yt_dlp_version": version,
        "status": "candidate",
    }


def process_url(
    url: str, root: Path, list_only: bool, ffmpeg: str | None, yt_dlp,
    limits: DownloadLimits = DownloadLimits(),
) -> dict:
    logger = RunLogger()
    result = {"url": url, "status": "failed", "messages": logger.messages}
    try:
        previous_download = existing_download(root, url)
        if previous_download:
            return previous_download
        options = ydl_options(ffmpeg, logger, limits)
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=False)
        if not info:
            raise ValueError("No metadata returned by the extractor.")
        if info.get("_type") in {"playlist", "multi_video"} or "entries" in info:
            raise ValueError("Collections/playlists are not downloaded. Supply individual video URLs.")
        if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming", "post_live"}:
            raise ValueError("Live or upcoming streams are not collected. Use a finished video.")
        if not list_only:
            check_metadata_limits(info, limits)
        metadata = provenance(info, url, yt_dlp.version.__version__)
        directory = root / safe_component(metadata["platform"]) / safe_component(metadata["platform_id"])
        directory = directory.resolve()
        if not directory.is_relative_to(root):
            raise ValueError("Unsafe output path.")
        directory.mkdir(parents=True, exist_ok=True)
        sidecar = directory / "source.json"
        result["source_json"] = str(sidecar.relative_to(PROJECT_ROOT))
        if sidecar.exists():
            previous = json.loads(sidecar.read_text(encoding="utf-8"))
            if previous.get("source_id") != metadata["source_id"]:
                raise ValueError("Existing folder has different provenance; refusing to overwrite.")
            if previous.get("file"):
                existing = (directory / previous["file"]).resolve()
                if not existing.is_relative_to(directory):
                    raise ValueError("Existing metadata points outside its source folder.")
                if existing.is_file():
                    result.update(status="already_downloaded", file=str(existing.relative_to(PROJECT_ROOT)))
                    return result
            # Preserve notes and any rights assessment made by a person.
            previous.update({key: value for key, value in metadata.items() if key not in {"rights_status"}})
            metadata = previous
        write_json(sidecar, metadata)
        if list_only:
            result["status"] = "listed"
            return result
        if any(directory.glob("media.*")):
            # An interrupted .part can safely resume, but an unrecorded final file
            # must not be silently treated as this source or overwritten.
            finals = [path for path in directory.glob("media.*") if path.suffix not in {".part", ".ytdl"}]
            if finals:
                raise ValueError("Unrecorded media exists; inspect this source folder before retrying.")
        options["outtmpl"] = str(directory / "media.%(ext)s")
        options["progress_hooks"] = [ByteBudget(limits.max_bytes)]
        paths: list[str] = []

        def capture_final(filename: str) -> None:
            paths.append(filename)

        options["post_hooks"] = [capture_final]
        with yt_dlp.YoutubeDL(options) as downloader:
            downloaded = downloader.process_ie_result(info, download=True)
            possible = paths + [downloaded.get("filepath") or "", downloader.prepare_filename(downloaded)]
        final = next(
            (Path(path).resolve() for path in possible if path and Path(path).is_file()), None
        )
        if final is None:
            finals = [path for path in directory.glob("media.*") if path.suffix.lower() in {
                ".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".m4v", ".ts", ".3gp"
            }]
            if len(finals) == 1:
                final = finals[0].resolve()
        if final is None or not final.is_relative_to(directory):
            raise ValueError("Download finished without a safely identifiable local video file.")
        if final.stat().st_size == 0:
            raise ValueError("Downloaded file is empty.")
        if final.stat().st_size > limits.max_bytes:
            raise ValueError("Final file exceeds --max-size-mb; kept for inspection, not marked as downloaded.")
        metadata.update({
            "file": final.relative_to(directory).as_posix(),
            "downloaded_at": utc_now(),
            "status": "downloaded",
        })
        for key in ("duration", "fps", "width", "height"):
            if downloaded.get(key) is not None:
                metadata[key] = downloaded[key]
        write_json(sidecar, metadata)
        result.update(status="downloaded", file=str(final.relative_to(PROJECT_ROOT)))
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
    finally:
        result["messages"] = logger.messages[-80:]
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect individual public videos locally with yt-dlp; keep source metadata.",
        epilog="No cookies/login/DRM bypass. A listed license is not permission verification. "
        "Keep collected footage in local/; only publish footage for which you have permission.",
    )
    parser.add_argument("--url", action="append", default=[], help="Individual http(s) video URL; repeatable.")
    parser.add_argument("--urls-file", type=Path, help="UTF-8 text (one URL per line), or CSV with a url column.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_ROOT, help="Destination inside local/ (default: local/videos/raw).")
    parser.add_argument("--max-downloads", type=int, default=5, help="Maximum URLs to attempt, including list-only (default: 5).")
    parser.add_argument("--max-duration", type=float, default=600, help="Reject known source durations above this many seconds (default: 600).")
    parser.add_argument("--max-size-mb", type=float, default=500, help="Maximum selected media size per source in MiB (default: 500); partial files may remain after cancellation.")
    parser.add_argument("--max-height", type=int, default=1080, help="Prefer only formats at or below this height in pixels (default: 1080); unknown resolutions are allowed.")
    parser.add_argument("--list-only", action="store_true", help="Fetch candidate metadata and write source.json; do not download media.")
    args = parser.parse_args(argv)
    if args.max_downloads < 1:
        parser.error("--max-downloads must be positive.")
    if not math.isfinite(args.max_duration) or args.max_duration <= 0:
        parser.error("--max-duration must be a finite positive number.")
    if not math.isfinite(args.max_size_mb) or args.max_size_mb <= 0:
        parser.error("--max-size-mb must be a finite positive number.")
    if args.max_height < 1:
        parser.error("--max-height must be positive.")
    limits = DownloadLimits(args.max_duration, max(1, int(args.max_size_mb * 1024 * 1024)), args.max_height)
    try:
        urls = load_urls(args.url, args.urls_file)
        root = local_output_root(args.output_root)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if not urls:
        parser.error("Supply at least one --url or --urls-file.")
    try:
        import yt_dlp
        import yt_dlp.version
    except ImportError:
        print("Missing yt-dlp. Run setup-data.cmd, then use .venv\\Scripts\\python.exe.", file=sys.stderr)
        return 2
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        ffmpeg = None
    root.mkdir(parents=True, exist_ok=True)
    report_dir = root / "_runs"
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8] + ".json")
    selected = urls[:args.max_downloads]
    report = {
        "schema_version": 1, "started_at": utc_now(), "list_only": args.list_only,
        "yt_dlp_version": yt_dlp.version.__version__, "submitted_urls": len(urls),
        "limits": {"max_duration": limits.max_duration, "max_bytes": limits.max_bytes, "max_height": limits.max_height},
        "deferred_urls": urls[args.max_downloads:], "results": [],
    }
    for index, url in enumerate(selected, 1):
        print(f"[{index}/{len(selected)}] {url}", flush=True)
        result = process_url(url, root, args.list_only, ffmpeg, yt_dlp, limits)
        report["results"].append(result)
        write_json(report_path, report)
        print("  " + result["status"] + (": " + result["error"] if "error" in result else ""), flush=True)
    report["finished_at"] = utc_now()
    write_json(report_path, report)
    counts = {status: sum(item["status"] == status for item in report["results"]) for status in (
        "downloaded", "listed", "already_downloaded", "failed"
    )}
    print("Summary: " + ", ".join(f"{name}={count}" for name, count in counts.items()))
    print(f"Report: {report_path}")
    if report["deferred_urls"]:
        print(f"Deferred {len(report['deferred_urls'])} URLs due to --max-downloads; see report.")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
