"""Draft provenance, coach-label preservation, and safe review rendering."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import create_review as page
from scripts import import_assistant_reviews as reviews


class AssistantReviewTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / "local" / "test-runs"
        scratch.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="review-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        override = patch.object(reviews, "ROOT", self.root)
        override.start()
        self.addCleanup(override.stop)
        self.item = {"clip_id": "example", "clip_path": "local/clip.mp4", "encoded_duration": 15,
                     "label_path": "local/dataset/labels/example.json"}
        clip = self.root / self.item["clip_path"]
        clip.parent.mkdir(parents=True)
        clip.write_bytes(b"registered video content")
        self.item["clip_sha256"] = reviews.digest(clip)
        (self.root / "local/evidence.jpg").write_bytes(b"evidence image")
        self.label = {"clip_id": "example", "review": {"status": "reviewed", "summary": "Original coach decision"},
                      "boundary_review": "accepted", "capture": {"camera_view": "rear"}}
        self.draft = {"clip_id": "example", "author": "assistant", "status": "draft", "requires_coach_review": True,
                      "summary": "Partial observation", "review_method": "Timed frame sequences",
                      "coverage_notes": "One stroke sampled", "evidence_files": ["local/evidence.jpg"],
                      "criteria": {key: {"judgment": "unobservable", "confidence": "low",
                                         "reason_unobservable": "Occluded", "observations": []} for key in reviews.KEYS}}
        self.draft["criteria"]["follow_through"] = {
            "judgment": "good", "confidence": "medium", "reason_unobservable": "",
            "observations": [{"start_seconds": 3, "end_seconds": 4, "visible_fact": "Arm crosses trunk",
                              "interpretation": "Finish visible", "suggestion": "Keep the movement"}]}
        self.write("local/dataset/items/example.json", self.item)
        self.write(self.item["label_path"], self.label)
        self.write("local/draft.json", self.draft)

    def write(self, path, value):
        path = self.root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def read_label(self):
        return json.loads((self.root / self.item["label_path"]).read_text(encoding="utf-8"))

    def test_import_and_revision_preserve_coach_decisions_and_are_idempotent(self):
        self.assertTrue(reviews.import_draft("local/draft.json"))
        first = self.read_label()
        for key, value in self.label.items():
            self.assertEqual(first[key], value)
        before = (self.root / self.item["label_path"]).read_bytes()
        self.assertFalse(reviews.import_draft("local/draft.json"))
        self.assertEqual((self.root / self.item["label_path"]).read_bytes(), before)
        self.draft["summary"] = "Revised after closer viewing"
        self.write("local/draft.json", self.draft)
        self.assertTrue(reviews.import_draft("local/draft.json"))
        revised = self.read_label()
        self.assertEqual(revised["review"], self.label["review"])
        self.assertEqual(revised["assistant_review_history"], [first["assistant_review"]])

    def test_bad_times_or_authority_cannot_modify_labels(self):
        cases = []
        for time in (-1, 16, float("nan")):
            draft = copy.deepcopy(self.draft)
            draft["criteria"]["follow_through"]["observations"][0]["end_seconds"] = time
            cases.append(draft)
        draft = copy.deepcopy(self.draft)
        draft["status"] = "reviewed"
        cases.append(draft)
        draft = copy.deepcopy(self.draft)
        draft["evidence_files"] = ["../outside.jpg"]
        cases.append(draft)
        for draft in cases:
            self.write("local/draft.json", draft)
            with self.subTest(draft=draft), self.assertRaises(ValueError):
                reviews.import_draft("local/draft.json")
            self.assertEqual(self.read_label(), self.label)

    def test_changed_video_rejects_stale_review(self):
        (self.root / self.item["clip_path"]).write_bytes(b"replaced video")
        with self.assertRaisesRegex(ValueError, "Clip has changed"):
            reviews.import_draft("local/draft.json")
        self.assertEqual(self.read_label(), self.label)

    def test_text_is_escaped_and_draft_is_distinct_from_coach(self):
        self.draft["summary"] = '<script>alert("unsafe")</script>'
        output = page.review_details(self.draft, assistant=True)
        self.assertNotIn("<script>", output)
        self.assertIn("&lt;script&gt;", output)
        self.assertIn("待教练复核", output)
        self.assertIn('data-time="3"', output)
        self.assertIn("画面", output)
        self.assertIn("初步解释", output)


if __name__ == "__main__":
    unittest.main()
