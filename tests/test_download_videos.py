"""Offline regressions for downloader limits and preservation of local sources."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import download_videos as collector


class MetadataOnlyYDL:
    info = {}
    download_calls = 0

    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def extract_info(self, url, download):
        assert download is False
        return self.info

    def process_ie_result(self, *args, **kwargs):
        type(self).download_calls += 1
        raise AssertionError("This source must be stopped before download.")


class DownloaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.root_patch = patch.object(collector, "PROJECT_ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        MetadataOnlyYDL.download_calls = 0
        self.ytdlp = SimpleNamespace(YoutubeDL=MetadataOnlyYDL, version=SimpleNamespace(__version__="test"))
        self.url = "https://example.com/clip"

    def test_archive_replay_and_list_only_preserve_metadata_without_network(self):
        folder = self.root / "youtube" / "id"
        folder.mkdir(parents=True)
        (folder / "media.mp4").write_bytes(b"existing local media")
        sidecar = folder / "source.json"
        original = json.dumps({
            "url": self.url, "requested_url": self.url, "file": "media.mp4",
            "rights_status": "permission_recorded", "downloaded_at": "previous date",
            "coach_notes": "Keep these annotations.",
        }).encode()
        sidecar.write_bytes(original)
        with patch.object(MetadataOnlyYDL, "__init__", side_effect=AssertionError("No network needed")):
            for list_only in (False, True):
                result = collector.process_url(self.url, self.root, list_only, None, self.ytdlp)
                self.assertEqual(result["status"], "already_downloaded")
                self.assertEqual(sidecar.read_bytes(), original)

    def test_long_large_or_tall_sources_stop_before_download(self):
        for field, value in (("duration", 601), ("filesize", 501 * 1024 * 1024), ("height", 2160)):
            with self.subTest(field=field):
                MetadataOnlyYDL.info = {"id": "id", "extractor": "youtube", field: value}
                result = collector.process_url(self.url, self.root, False, None, self.ytdlp)
                self.assertEqual(result["status"], "failed")
                self.assertIn("max-", result["error"])
        self.assertEqual(MetadataOnlyYDL.download_calls, 0)

    def test_playlist_never_downloads_and_long_candidate_can_be_listed(self):
        MetadataOnlyYDL.info = {"_type": "playlist", "entries": iter([{"id": "id"}])}
        result = collector.process_url(self.url, self.root, False, None, self.ytdlp)
        self.assertEqual(result["status"], "failed")
        self.assertIn("Collections/playlists", result["error"])
        MetadataOnlyYDL.info = {"id": "id", "extractor": "youtube", "duration": 3600}
        result = collector.process_url(self.url, self.root, True, None, self.ytdlp)
        self.assertEqual(result["status"], "listed")
        metadata = json.loads((self.root / result["source_json"]).read_text(encoding="utf-8"))
        self.assertIsNone(metadata["file"])
        self.assertEqual(metadata["duration"], 3600)
        self.assertEqual(MetadataOnlyYDL.download_calls, 0)

    def test_byte_budget_counts_each_stream_without_recounting_progress(self):
        budget = collector.ByteBudget(100)
        budget({"filename": "video", "downloaded_bytes": 50})
        budget({"filename": "video", "downloaded_bytes": 60})
        budget({"filename": "video", "downloaded_bytes": 60})
        budget({"filename": "audio", "downloaded_bytes": 30})
        with self.assertRaisesRegex(ValueError, "max-size-mb"):
            budget({"filename": "audio", "downloaded_bytes": 41})
        with self.assertRaisesRegex(ValueError, "max-size-mb"):
            collector.check_metadata_limits({"requested_formats": [{"filesize": 60}, {"filesize": 60}]},
                                            collector.DownloadLimits(max_bytes=100))


if __name__ == "__main__":
    unittest.main()
