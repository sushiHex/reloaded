from __future__ import annotations

import json
import pathlib

import os
import time

import reloaded.transcript as transcript_mod
from reloaded.transcript import (
    human_size,
    is_tail_torn,
    prune_torn_backups,
    repair_torn_tail,
)


def _write(path, lines, trailing_newline=True):
    data = "".join(json.dumps(o) + "\n" for o in lines)
    if not trailing_newline:
        data = data.rstrip("\n")
    path.write_text(data, encoding="utf-8")


def test_human_size_formats_megabytes_and_gigabytes():
    assert human_size(1024 * 1024) == "1 MB"
    assert human_size(483 * 1024 * 1024) == "483 MB"
    assert human_size(2 * 1024 * 1024 * 1024) == "2.0 GB"


def test_intact_transcript_is_not_torn(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [{"a": 1}, {"b": 2}])
    assert is_tail_torn(p) is False


def test_transcript_without_trailing_newline_but_valid_json_is_not_torn(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [{"a": 1}, {"b": 2}], trailing_newline=False)
    assert is_tail_torn(p) is False


def test_truncated_final_line_is_detected_as_torn(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [{"a": 1}])
    with open(p, "a", encoding="utf-8") as fh:
        fh.write('{"type":"user","cwd":"C:\\\\Us')
    assert is_tail_torn(p) is True


def test_repair_removes_the_partial_line_and_keeps_everything_before(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [{"a": 1}, {"b": 2}])
    with open(p, "a", encoding="utf-8") as fh:
        fh.write('{"c":')
    assert repair_torn_tail(p) is True

    lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert [json.loads(l) for l in lines] == [{"a": 1}, {"b": 2}]
    assert is_tail_torn(p) is False


def test_repair_saves_the_removed_bytes_beside_the_transcript(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [{"a": 1}])
    with open(p, "a", encoding="utf-8") as fh:
        fh.write('{"c":')
    repair_torn_tail(p)
    backup = pathlib.Path(str(p) + ".torn")
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == '{"c":'


def test_repair_is_a_noop_on_an_intact_transcript(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [{"a": 1}, {"b": 2}])
    before = p.read_text(encoding="utf-8")
    assert repair_torn_tail(p) is False
    assert p.read_text(encoding="utf-8") == before
    assert not pathlib.Path(str(p) + ".torn").exists()


def test_repair_handles_an_empty_file(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text("", encoding="utf-8")
    assert repair_torn_tail(p) is False


def _age(path, days_old: float) -> None:
    old = time.time() - days_old * 86400
    os.utime(path, (old, old))


def test_prune_removes_torn_files_older_than_the_cutoff(tmp_path):
    old = tmp_path / "sess1.jsonl.torn"
    old.write_bytes(b"partial")
    _age(old, 45)
    removed = prune_torn_backups(tmp_path, max_age_days=30)
    assert removed == [str(old)]
    assert not old.exists()


def test_prune_leaves_recent_torn_files_alone(tmp_path):
    recent = tmp_path / "sess2.jsonl.torn"
    recent.write_bytes(b"partial")
    _age(recent, 5)
    removed = prune_torn_backups(tmp_path, max_age_days=30)
    assert removed == []
    assert recent.exists()


def test_prune_only_touches_torn_files_not_transcripts(tmp_path):
    transcript = tmp_path / "sess3.jsonl"
    transcript.write_text('{"a": 1}\n', encoding="utf-8")
    _age(transcript, 400)  # ancient, but not a .torn file
    prune_torn_backups(tmp_path, max_age_days=30)
    assert transcript.exists()


def test_prune_recurses_into_project_subdirectories(tmp_path):
    sub = tmp_path / "C--Users-k-repos-editor-app"
    sub.mkdir()
    old = sub / "sess4.jsonl.torn"
    old.write_bytes(b"partial")
    _age(old, 45)
    removed = prune_torn_backups(tmp_path, max_age_days=30)
    assert removed == [str(old)]


def test_prune_handles_a_missing_root_without_crashing(tmp_path):
    assert prune_torn_backups(tmp_path / "does-not-exist") == []


def test_repair_computes_the_offset_exactly_once(tmp_path, monkeypatch):
    """Regression guard: repair_torn_tail must not recompute the offset.

    An earlier version called last_line_offset once inside is_tail_torn and
    again separately before truncating. If the file changed size between the
    two calls, the offset used to decide "torn" and the offset used to
    truncate could disagree, letting the truncate discard more than the
    torn-ness check ever looked at. Pinning the call count keeps that fix in
    place even if this module is edited again.
    """
    p = tmp_path / "s.jsonl"
    _write(p, [{"a": 1}])
    with open(p, "a", encoding="utf-8") as fh:
        fh.write('{"c":')

    calls = []
    real = transcript_mod.last_line_offset

    def counting(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(transcript_mod, "last_line_offset", counting)
    assert repair_torn_tail(p) is True
    assert len(calls) == 1, f"expected exactly one offset computation, got {len(calls)}"
