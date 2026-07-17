"""Progress reporter atomic writes/counters + staleness + card snapshots."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from packages.wiki_core.ingest.progress import (
    FileProgressReporter,
    is_stale,
    read_progress,
    write_queued,
)
from teams_bot.cards import build_sync_progress_card


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_reporter_counts_outcomes(tmp_path):
    path = tmp_path / "p.json"
    r = FileProgressReporter(path, job_id="j1", job_type="manual", requested_by_user_name="Dana")
    r.start()
    r.set_total(4)
    for outcome, p in [("updated", "a"), ("empty", "b"), ("failed", "c"), ("skipped_unchanged", "d")]:
        r.begin_file(p)
        r.record(outcome, path=p, error="boom" if outcome == "failed" else None)

    rec = _read(path)
    assert rec["status"] == "running"
    assert rec["files_total"] == 4
    assert rec["updated_files"] == 1
    assert rec["empty_files"] == 1
    assert len(rec["failed_files"]) == 1 and rec["failed_files"][0]["path"] == "c"
    assert rec["skipped_unchanged"] == 1
    # files_done counts every file examined, including unchanged skips.
    assert rec["files_done"] == 4


def test_reporter_finish_ok_and_error(tmp_path):
    path = tmp_path / "p.json"
    r = FileProgressReporter(path, job_id="j", job_type="manual")
    r.start()
    r.finish_ok()
    assert _read(path)["status"] == "completed"

    r2 = FileProgressReporter(path, job_id="j2", job_type="manual")
    r2.start()
    r2.finish_error("kaboom")
    rec = _read(path)
    assert rec["status"] == "failed" and rec["error"] == "kaboom"


def test_read_progress_tolerates_missing_and_corrupt(tmp_path):
    path = tmp_path / "missing.json"
    assert read_progress(path) is None
    path.write_text("{not json", encoding="utf-8")
    assert read_progress(path) is None


def test_write_queued_produces_fresh_queued_record(tmp_path):
    path = tmp_path / "p.json"
    write_queued(path, job_id="q1", job_type="manual", requested_by_user_name="Dana")
    rec = read_progress(path)
    assert rec["status"] == "queued" and rec["job_id"] == "q1"
    assert not is_stale(rec)


def test_is_stale_by_heartbeat_age():
    old = {"status": "running", "updated_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat()}
    fresh = {"status": "running", "updated_at": datetime.now(UTC).isoformat()}
    done = {"status": "completed", "updated_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat()}
    assert is_stale(old) is True
    assert is_stale(fresh) is False
    assert is_stale(done) is False  # terminal states are never "stale"


def _card_text(attachment) -> str:
    # CardFactory wraps the dict in attachment.content; flatten it back to text.
    return json.dumps(attachment.content)


def test_card_renders_each_state():
    queued = build_sync_progress_card({"status": "queued", "job_id": "abc123ef", "requested_by_user_name": "Dana"})
    assert "queued" in _card_text(queued).lower()

    running = build_sync_progress_card(
        {"status": "running", "phase": "processing", "files_total": 10, "files_done": 4, "updated_files": 3, "job_id": "j"}
    )
    text = _card_text(running)
    assert "in progress" in text.lower()
    assert "40%" in text  # 4/10 progress bar

    stalled = build_sync_progress_card({"status": "running", "phase": "processing", "job_id": "j"}, stalled=True)
    assert "stalled" in _card_text(stalled).lower()

    completed = build_sync_progress_card(
        {
            "status": "completed",
            "updated_files": 5,
            "failed_files": [{"path": "raw/sources/x.pdf", "error": "BadPdf"}],
            "unsupported_files": {".mov": 2},
            "job_id": "j",
        }
    )
    ctext = _card_text(completed)
    assert "complete" in ctext.lower()
    assert "x.pdf" in ctext and "BadPdf" in ctext
    assert ".mov" in ctext


def test_completed_card_caps_failed_list():
    failed = [{"path": f"raw/sources/f{i}.pdf", "error": "E"} for i in range(40)]
    card = build_sync_progress_card({"status": "completed", "failed_files": failed, "job_id": "j"})
    text = _card_text(card)
    assert "and 15 more" in text  # 40 - 25 cap
