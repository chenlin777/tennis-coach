"""Download pinned local MediaPipe assets; never read or upload user videos."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tarfile
import tempfile
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "web" / "vendor"
MANIFEST = Path(__file__).with_name("auto_assets.json")
HOSTS = {"registry.npmjs.org", "storage.googleapis.com", "raw.githubusercontent.com"}


def validate_url(url: str) -> None:
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname not in HOSTS
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError(f"Unapproved download URL: {url}")


class OfficialRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_target(relative: str) -> Path:
    parts = PurePosixPath(relative)
    if (parts.is_absolute() or not parts.parts or ".." in parts.parts
            or "\\" in relative or ":" in relative):
        raise ValueError(f"Unsafe asset path: {relative}")
    target = VENDOR.joinpath(*parts.parts)
    if not target.resolve().is_relative_to(VENDOR.resolve()):
        raise ValueError(f"Asset path escapes vendor: {relative}")
    return target


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def matches(path: Path, expected: str) -> bool:
    return path.is_file() and sha256(path.read_bytes()) == expected


def download(url: str, expected_size: int) -> bytes:
    validate_url(url)
    print(f"Downloading {url}", flush=True)
    request = Request(url, headers={"User-Agent": "tennis-coach-asset-setup/1"})
    with build_opener(OfficialRedirects()).open(request, timeout=45) as response:
        validate_url(response.url)
        payload = response.read(expected_size + 1)
    if len(payload) != expected_size:
        raise ValueError(f"Unexpected download size for {url}")
    return payload


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".install-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def package_outputs(package: dict, payload: bytes) -> dict[Path, bytes]:
    algorithm, expected = package["integrity"].split("-", 1)
    if algorithm != "sha512":
        raise ValueError("Package integrity must use SHA-512")
    actual = base64.b64encode(hashlib.sha512(payload).digest()).decode("ascii")
    if actual != expected:
        raise ValueError("MediaPipe npm archive failed SHA-512 integrity check")
    outputs = {}
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        members = {}
        for member in archive.getmembers():
            if member.name in members:
                raise ValueError(f"Duplicate archive entry: {member.name}")
            members[member.name] = member
        # Exact manifest whitelist. No extract()/extractall(), links or arbitrary paths.
        for relative, expected_hash in package["files"].items():
            target = safe_target("mediapipe/" + relative)
            member = members.get("package/" + relative)
            if member is None or not member.isfile() or member.issym() or member.islnk():
                raise ValueError(f"Missing or unsafe archive entry: {relative}")
            with archive.extractfile(member) as handle:
                content = handle.read()
            if sha256(content) != expected_hash:
                raise ValueError(f"Package file failed SHA-256 check: {relative}")
            outputs[target] = content
    metadata = json.loads(outputs[safe_target("mediapipe/package.json")])
    if metadata.get("name") != package["name"] or metadata.get("version") != package["version"]:
        raise ValueError("Package identity does not match the manifest")
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify all installed files without network access")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest["schema_version"] != 1:
        raise ValueError("Unsupported asset manifest")
    package = manifest["package"]
    required = [(safe_target("mediapipe/" + name), value) for name, value in package["files"].items()]
    required.extend((safe_target(item["path"]), item["sha256"]) for item in manifest["assets"])
    missing = [path for path, value in required if not matches(path, value)]
    if args.check:
        for path in missing:
            print(f"Missing or changed: {path.relative_to(ROOT)}")
        print(f"Verified {len(required) - len(missing)}/{len(required)} local asset files.")
        return 1 if missing else 0
    if not missing:
        print(f"All {len(required)} asset files verified. No downloads needed.")
        return 0

    # Stage and validate every missing component before replacing any installed file.
    pending = {}
    if any(not matches(safe_target("mediapipe/" + name), value) for name, value in package["files"].items()):
        pending.update(package_outputs(package, download(package["url"], package["bytes"])))
    for item in manifest["assets"]:
        path = safe_target(item["path"])
        if matches(path, item["sha256"]):
            continue
        payload = download(item["url"], item["bytes"])
        if sha256(payload) != item["sha256"]:
            raise ValueError(f"Asset failed SHA-256 check: {item['path']}")
        pending[path] = payload
    for path, payload in pending.items():
        atomic_write(path, payload)
    if any(not matches(path, expected) for path, expected in required):
        raise ValueError("Final asset verification failed")
    print(f"Installed and verified all {len(required)} local asset files.")
    print("Videos stay on your device. Run start-privacy.cmd to open the app.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, tarfile.TarError) as error:
        print(f"Setup failed: {error}", file=sys.stderr)
        print("No unverified download was installed. You can retry setup-auto.cmd.", file=sys.stderr)
        sys.exit(1)
