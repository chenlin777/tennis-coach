"""Clip planning, provenance, and a real local video/audio round trip."""

from contextlib import redirect_stderr, redirect_stdout
import csv
import io
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import prepare_clips as clips


REPOSITORY = Path(__file__).resolve().parents[1]


class WindowPlanningTests(unittest.TestCase):
    def test_fifty_second_video_has_three_complete_adjacent_clips(self):
        windows = clips.plan_windows(0, 50.1)
        self.assertEqual(windows, [(0, 16.7), (16.7, 33.4), (33.4, 50.1)])
        self.assertAlmostEqual(sum(end - start for start, end in windows), 50.1)
        self.assertTrue(all(math.isclose(end - start, 16.7) for start, end in windows))

    def test_bounds_and_short_source_are_preserved(self):
        for duration in (10, 20, 20.1, 29.9, 40.1, 101):
            with self.subTest(duration=duration):
                windows = clips.plan_windows(7, 7 + duration)
                self.assertEqual(windows[0][0], 7)
                self.assertEqual(windows[-1][1], 7 + duration)
                for index, (start, end) in enumerate(windows):
                    self.assertGreaterEqual(end - start, 10 - 1e-8)
                    self.assertLessEqual(end - start, 20 + 1e-8)
                    if index:
                        self.assertEqual(windows[index - 1][1], start)
        self.assertEqual(clips.plan_windows(4, 9), [(4, 9)])

    def test_invalid_times_and_incompatible_limits_fail(self):
        cases = [
            (-1, 20, 15, 10, 20), (20, 20, 15, 10, 20),
            (0, 30, math.nan, 10, 20), (0, math.inf, 15, 10, 20),
            (0, 30, 15, 0, 20), (0, 30, 25, 10, 20),
            (0, 21, 15, 15, 20),
        ]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                clips.plan_windows(*values)


class IsolatedDatasetTests(unittest.TestCase):
    def setUp(self):
        scratch = REPOSITORY / "local" / "test-runs"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="clips-", dir=scratch)
        self.root = Path(self.temporary.name).resolve()
        self.assertTrue(self.root.is_relative_to(scratch.resolve()))
        self.addCleanup(self.temporary.cleanup)
        (self.root / "templates").mkdir()
        shutil.copy2(REPOSITORY / "templates" / "clip-label.json", self.root / "templates" / "clip-label.json")
        self.local = self.root / "local"
        self.raw = self.local / "videos" / "raw"
        self.output = self.local / "videos" / "clips"
        self.dataset = self.local / "dataset"
        override = patch.multiple(clips, ROOT=self.root, LOCAL=self.local, RAW=self.raw,
                                  CLIPS=self.output, DATASET=self.dataset)
        override.start()
        self.addCleanup(override.stop)
        self.input = self.root / "test-source.mp4"

    def run_main(self, *arguments):
        with patch("sys.argv", ["prepare_clips.py", "--input", str(self.input), *arguments]), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return clips.main()

    def test_invalid_window_is_rejected_before_encoding(self):
        self.input.write_bytes(b"input existence only; import metadata is mocked")
        source = {"duration": 12, "source_id": "test:window", "sha256": "mock-fingerprint"}
        with patch.object(clips, "import_raw", return_value=(self.input, source)), \
                patch.object(clips, "prepare") as prepare:
            for arguments in (("--end", "13"), ("--start", "13"),
                              ("--start", "nan"), ("--end", "inf")):
                with self.subTest(arguments=arguments):
                    self.assertEqual(self.run_main(*arguments), 1)
            prepare.assert_not_called()
        self.assertFalse(list(self.output.rglob("*.mp4")))

    def test_multiple_unregistered_raw_files_get_independent_archives(self):
        self.raw.mkdir(parents=True)
        first = self.raw / "forehand-one.mp4"
        second = self.raw / "forehand-two.mp4"
        first.write_bytes(b"first original; video probing is mocked")
        second.write_bytes(b"second original; video probing is mocked")
        expected = {first: first.read_bytes(), second: second.read_bytes()}
        metadata = {"duration": 15, "fps": 30, "width": 160, "height": 120}
        with patch.object(clips, "probe", return_value=metadata):
            archives = [clips.import_raw(path) for path in (first, second)]
            self.assertNotEqual(archives[0][0].parent, archives[1][0].parent)
            self.assertNotEqual(archives[0][1]["source_id"], archives[1][1]["source_id"])
            self.assertFalse((self.raw / "source.json").exists())
            for original, (archived, source) in zip((first, second), archives):
                self.assertTrue(archived.is_relative_to(self.raw / "local"))
                self.assertEqual(original.read_bytes(), expected[original])
                self.assertEqual(archived.read_bytes(), expected[original])
                sidecar = json.loads((archived.parent / "source.json").read_text(encoding="utf-8"))
                self.assertEqual(sidecar["file"], archived.name)
                self.assertEqual(sidecar["sha256"], source["sha256"])
            # A later folder scan sees both dropped originals and their stored
            # copies, but should submit each source only once for clipping.
            with patch.object(clips, "prepare", return_value=0) as prepare, \
                    patch("sys.argv", ["prepare_clips.py", "--input", str(self.raw)]), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(clips.main(), 0)
                self.assertEqual(prepare.call_count, 2)

    def test_directory_scan_ignores_unfinished_download_streams(self):
        failed = self.raw / "youtube" / "failed"
        failed.mkdir(parents=True)
        (failed / "source.json").write_text(json.dumps({"file": None}), encoding="utf-8")
        (failed / "media.f137.mp4").write_bytes(b"incomplete video stream")
        complete = self.raw / "youtube" / "complete"
        complete.mkdir(parents=True)
        (complete / "source.json").write_text(json.dumps({"file": "media.mp4"}), encoding="utf-8")
        video = complete / "media.mp4"
        video.write_bytes(b"registered completed media")
        (complete / "media.f137.mp4").write_bytes(b"leftover stream")
        self.assertEqual(clips.collect_inputs([str(self.raw)]), [video])

    def test_real_clip_retains_audio_provenance_and_manual_labels_on_rerun(self):
        try:
            import imageio_ffmpeg
        except ImportError:
            self.skipTest("Install imageio-ffmpeg with setup-data.cmd to test actual encoding")
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        creation = subprocess.run([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
            "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=20",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
            "-t", "12", "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", str(self.input),
        ], capture_output=True, timeout=30)
        self.assertEqual(creation.returncode, 0, creation.stderr.decode(errors="replace"))
        original_digest = clips.digest_file(self.input)

        self.assertEqual(self.run_main("--start", "1", "--end", "11.5"), 0)
        items = list((self.dataset / "items").glob("*.json"))
        self.assertEqual(len(items), 1)
        item = json.loads(items[0].read_text(encoding="utf-8"))
        imported = self.root / item["parent_raw_path"]
        output = self.root / item["clip_path"]
        label_path = self.root / item["label_path"]
        label = json.loads(label_path.read_text(encoding="utf-8"))
        self.assertTrue(imported.is_relative_to(self.raw))
        self.assertEqual(clips.digest_file(imported), original_digest)
        self.assertEqual(clips.digest_file(self.input), original_digest)
        self.assertEqual((item["start_seconds"], item["end_seconds"]), (1, 11.5))
        for key in ("source_id", "source_group_id", "parent_raw_path", "clip_path", "start_seconds", "end_seconds"):
            self.assertEqual(label[key], item[key])
        self.assertEqual(label["review"]["status"], "unreviewed")
        self.assertIsNone(label["review"]["overall_judgment"])
        self.assertEqual(label["boundary_review"], "pending")

        reader = imageio_ffmpeg.read_frames(str(output))
        try:
            metadata = next(reader)
            frame_count = sum(1 for _ in reader)
        finally:
            reader.close()
        self.assertAlmostEqual(metadata["duration"], 10.5, delta=0.15)
        self.assertAlmostEqual(frame_count / metadata["fps"], 10.5, delta=0.1)
        audio = subprocess.run([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(output),
            "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1",
        ], capture_output=True, timeout=30)
        self.assertEqual(audio.returncode, 0, audio.stderr.decode(errors="replace"))
        self.assertAlmostEqual(len(audio.stdout) / (16000 * 2), 10.5, delta=0.15)
        self.assertTrue(any(audio.stdout), "Preserved audio must not be silent PCM")

        label["review"].update(status="reviewed", overall_judgment="context_dependent", summary="Keep the coach's manual review.")
        label["boundary_review"] = "accepted"
        label_path.write_text(json.dumps(label, ensure_ascii=False, indent=2), encoding="utf-8")
        edited_label_bytes = label_path.read_bytes()
        output_digest = clips.digest_file(output)
        with patch.object(clips, "encode_clip", side_effect=AssertionError("Rerun must reuse registered clip")):
            self.assertEqual(self.run_main("--start", "1", "--end", "11.5"), 0)
        self.assertEqual(label_path.read_bytes(), edited_label_bytes)
        self.assertEqual(clips.digest_file(output), output_digest)
        self.assertEqual(len(list(self.output.rglob("*.mp4"))), 1)
        with (self.dataset / "clips.csv").open(encoding="utf-8-sig", newline="") as stream:
            indexed = list(csv.DictReader(stream))
        self.assertEqual(len(indexed), 1)
        self.assertEqual(indexed[0]["review_status"], "reviewed")
        self.assertEqual(indexed[0]["boundary_review"], "accepted")


if __name__ == "__main__":
    unittest.main()
