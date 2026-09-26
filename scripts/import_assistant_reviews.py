"""Attach evidence-based assistant drafts without changing the coach's review.

This imports completed reviews; it does not analyze video or generate judgments.
"""

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEYS = (
    "eyes_follow_ball", "non_dominant_arm", "contact_in_front",
    "follow_through", "knee_bend_and_body_height", "movement_and_positioning",
)
JUDGMENTS = {"good", "needs_improvement", "unobservable", "context_dependent"}


def local_path(value):
    path = (ROOT / value).resolve()
    if not path.is_relative_to((ROOT / "local").resolve()):
        raise ValueError("Review files must stay inside local/.")
    return path


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def validate(draft, item):
    if draft.get("clip_id") != item["clip_id"]:
        raise ValueError("Draft refers to a different clip.")
    if (draft.get("author") != "assistant" or draft.get("status") != "draft"
            or draft.get("requires_coach_review") is not True):
        raise ValueError("Assistant judgments must remain explicitly marked as drafts.")
    for key in ("summary", "review_method", "coverage_notes"):
        if not isinstance(draft.get(key), str) or not draft[key].strip():
            raise ValueError(f"Missing {key}.")
    criteria = draft.get("criteria", {})
    if set(criteria) != set(KEYS):
        raise ValueError("A draft must cover all six coaching criteria.")
    duration = item["encoded_duration"]
    for key, criterion in criteria.items():
        judgment = criterion.get("judgment")
        if judgment not in JUDGMENTS:
            raise ValueError(f"Invalid judgment: {key}.")
        if criterion.get("confidence") not in {"high", "medium", "low"}:
            raise ValueError(f"Missing confidence: {key}.")
        observations = criterion.get("observations", [])
        if judgment == "unobservable":
            if not criterion.get("reason_unobservable"):
                raise ValueError(f"Explain why {key} cannot be judged.")
        elif not observations:
            raise ValueError(f"Judgment needs timestamped evidence: {key}.")
        for observation in observations:
            start, end = observation.get("start_seconds"), observation.get("end_seconds")
            if not all(type(value) in {int, float} and math.isfinite(value) for value in (start, end)):
                raise ValueError("Evidence times must be finite numbers.")
            if not 0 <= start <= end <= duration:
                raise ValueError("Evidence must use seconds within the reviewed clip.")
            if not observation.get("visible_fact"):
                raise ValueError("Evidence needs a visible fact.")
    if not draft.get("evidence_files"):
        raise ValueError("Record the frames or sequences inspected for the draft.")
    for evidence in draft["evidence_files"]:
        if not local_path(evidence).is_file():
            raise ValueError(f"Missing local evidence: {evidence}.")


def import_draft(path):
    path = local_path(path)
    draft = json.loads(path.read_text(encoding="utf-8"))
    # Look up by filename only after disallowing path components in clip IDs.
    clip_id = draft.get("clip_id", "")
    if not clip_id or any(c in clip_id for c in "/\\:") or clip_id in {".", ".."}:
        raise ValueError("Invalid clip ID.")
    item_path = local_path(Path("local/dataset/items") / (clip_id + ".json"))
    item = json.loads(item_path.read_text(encoding="utf-8"))
    validate(draft, item)
    clip_path = local_path(item["clip_path"])
    actual_hash = digest(clip_path)
    if actual_hash != item["clip_sha256"]:
        raise ValueError("Clip has changed since it was registered; review the new version first.")
    label_path = local_path(item["label_path"])
    label = json.loads(label_path.read_text(encoding="utf-8"))
    if label["clip_id"] != clip_id:
        raise ValueError("Label refers to a different clip.")
    enriched = dict(draft, rules_version="coach-forehand-v1",
                    reviewed_file_path=item["clip_path"], reviewed_version="clip",
                    reviewed_sha256=actual_hash,
                    draft_file=path.relative_to(ROOT).as_posix())
    old = label.get("assistant_review")
    if old and all(old.get(key) == value for key, value in enriched.items()):
        return False
    if old:
        label.setdefault("assistant_review_history", []).append(old)
    enriched["imported_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    label["assistant_review"] = enriched
    # Only the separate assistant field/history changes. Coach review stays intact.
    temporary = label_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(label, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(label_path)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, action="append", help="Draft JSON or directory under local/.")
    args = parser.parse_args()
    paths = []
    for value in args.input:
        path = local_path(value)
        paths.extend(sorted(path.glob("*.json")) if path.is_dir() else [path])
    if not paths:
        parser.error("No draft JSON files found.")
    changed = sum(import_draft(path) for path in paths)
    if __package__:
        from .prepare_clips import rebuild_index
    else:
        from prepare_clips import rebuild_index
    rebuild_index()
    print(f"Imported {changed} assistant drafts; coach reviews unchanged.")


if __name__ == "__main__":
    main()
