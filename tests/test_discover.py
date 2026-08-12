from __future__ import annotations

import json
import os
import time
import types

from reloaded.discover import (
    read_transcript_info,
    strip_glyph,
    title_to_cwd,
    transcript_index,
)
from reloaded.paths import norm


def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def test_strip_glyph_removes_claude_spinner_prefixes():
    assert strip_glyph("✳ editor-app") == "editor-app"
    assert strip_glyph("⠐ computers") == "computers"
    assert strip_glyph("⠂ demo-app") == "demo-app"
    assert strip_glyph("plain") == "plain"


def test_strip_glyph_removes_circle_spinner_frames():
    """The frames Claude Code actually ships now.

    An earlier explicit-frame list covered only ✳ and the Braille dots, so a
    busy tab kept its glyph, stopped matching its repo basename, and was
    dropped by `capture` - silently, because the tests only exercised the
    frames the list already had.
    """
    for frame in "◐◑◒◓":
        assert strip_glyph(f"{frame} computers") == "computers"


def test_strip_glyph_survives_an_unknown_future_spinner_frame():
    # ◴ (U+25F4) is not a frame anything ships today; matching by Unicode
    # category rather than a literal list is what keeps this passing.
    assert strip_glyph("◴ computers") == "computers"


def test_strip_glyph_keeps_titles_that_merely_start_with_punctuation():
    assert strip_glyph("-dash-lead") == "-dash-lead"
    assert strip_glyph("_underscore") == "_underscore"
    assert strip_glyph("2fa-service") == "2fa-service"


def test_strip_glyph_preserves_inner_text_and_spacing():
    assert strip_glyph("⠐ Resume editor-app project") == "Resume editor-app project"


def test_read_transcript_info_extracts_cwd_and_title(tmp_path):
    p = tmp_path / "s.jsonl"
    _write_jsonl(
        p,
        [
            {"type": "user", "cwd": r"C:\Users\k\repos\editor-app", "uuid": "1"},
            {"type": "meta", "customTitle": "editor-app"},
            {"type": "user", "cwd": r"C:\Users\k\repos\editor-app", "uuid": "2"},
        ],
    )
    info = read_transcript_info(p)
    assert info is not None
    assert info.cwd == r"C:\Users\k\repos\editor-app"
    assert info.title == "editor-app"


def test_read_transcript_info_takes_the_last_custom_title(tmp_path):
    # A session renamed mid-flight must report its current name.
    p = tmp_path / "s.jsonl"
    _write_jsonl(
        p,
        [
            {"cwd": r"C:\Users\k\repos\editor-app"},
            {"customTitle": "old name"},
            {"customTitle": "Resume editor-app project from previous session"},
        ],
    )
    info = read_transcript_info(p)
    assert info.title == "Resume editor-app project from previous session"


def test_read_transcript_info_without_custom_title_returns_empty_title(tmp_path):
    p = tmp_path / "s.jsonl"
    _write_jsonl(p, [{"cwd": r"C:\Users\k\repos\sample-mcp"}])
    info = read_transcript_info(p)
    assert info.cwd == r"C:\Users\k\repos\sample-mcp"
    assert info.title == ""


def test_read_transcript_info_survives_a_torn_final_line(tmp_path):
    p = tmp_path / "s.jsonl"
    _write_jsonl(p, [{"cwd": r"C:\Users\k\repos\editor-app"}, {"customTitle": "editor-app"}])
    with open(p, "a", encoding="utf-8") as fh:
        fh.write('{"type":"user","cwd":"C:\\\\Users')  # truncated, no newline
    info = read_transcript_info(p)
    assert info is not None
    assert info.title == "editor-app"


def test_need_title_false_skips_the_title_but_keeps_cwd_and_size(tmp_path):
    """The performance branch: size-only callers must still get cwd and size."""
    p = tmp_path / "s.jsonl"
    _write_jsonl(p, [{"cwd": r"C:\Users\k\repos\editor-app"}, {"customTitle": "editor-app"}])

    full = read_transcript_info(p)
    slim = read_transcript_info(p, need_title=False)

    assert full.title == "editor-app"
    assert slim.title == "", "title must be skipped, not read"
    assert slim.cwd == full.cwd
    assert slim.size == full.size


def test_need_title_false_still_finds_cwd_when_only_the_tail_has_it(tmp_path):
    """cwd is mandatory, so the tail is read even in slim mode if the head missed it."""
    p = tmp_path / "s.jsonl"
    _write_jsonl(p, [{"type": "meta"}, {"cwd": r"C:\Users\k\repos\editor-app"}])
    info = read_transcript_info(p, need_title=False)
    assert info is not None
    assert info.cwd == r"C:\Users\k\repos\editor-app"


def test_transcript_index_propagates_need_title(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    _write_jsonl(d / "a.jsonl", [{"cwd": r"C:\p"}, {"customTitle": "titled"}])
    assert transcript_index(tmp_path)[norm(r"C:\p")].title == "titled"
    assert transcript_index(tmp_path, need_title=False)[norm(r"C:\p")].title == ""


def test_transcript_index_is_keyed_by_normalized_cwd(tmp_path):
    d = tmp_path / "C--Users-k-repos-editor-app"
    d.mkdir()
    _write_jsonl(d / "a.jsonl", [{"cwd": r"C:\Users\k\repos\editor-app"}, {"customTitle": "editor-app"}])
    idx = transcript_index(tmp_path)
    assert norm(r"C:\Users\k\repos\editor-app") in idx


def test_transcript_index_picks_the_newest_transcript_per_project(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    _write_jsonl(d / "old.jsonl", [{"cwd": r"C:\p"}, {"customTitle": "stale"}])
    time.sleep(0.01)
    _write_jsonl(d / "new.jsonl", [{"cwd": r"C:\p"}, {"customTitle": "current"}])
    idx = transcript_index(tmp_path)
    assert idx[norm(r"C:\p")].title == "current"


def test_transcript_index_uses_cwd_from_json_not_the_directory_name(tmp_path):
    # The directory encoding replaces both "\" and ":" with "-", which is lossy
    # for any path containing a dash. The JSON cwd is authoritative.
    d = tmp_path / "C--Users-k-repos-widget-cli"
    d.mkdir()
    _write_jsonl(
        d / "a.jsonl",
        [{"cwd": r"C:\Users\k\repos\widget-cli"}, {"customTitle": "widget-cli"}],
    )
    idx = transcript_index(tmp_path)
    assert idx[norm(r"C:\Users\k\repos\widget-cli")].cwd == r"C:\Users\k\repos\widget-cli"


class _FakeEntry:
    """Duck-types the subset of os.DirEntry that transcript_index touches."""

    def __init__(self, name, path, is_dir=False, is_file=False, mtime=0.0, raise_on_stat=False):
        self.name = name
        self.path = path
        self._is_dir = is_dir
        self._is_file = is_file
        self._mtime = mtime
        self._raise = raise_on_stat

    def is_dir(self):
        return self._is_dir

    def is_file(self):
        return self._is_file

    def stat(self):
        if self._raise:
            raise OSError("file vanished between listing and stat")
        return types.SimpleNamespace(st_mtime=self._mtime, st_size=123)


class _FakeScandir:
    def __init__(self, entries):
        self._entries = entries

    def __enter__(self):
        return iter(self._entries)

    def __exit__(self, *exc):
        return False


def test_transcript_index_survives_a_file_vanishing_between_listing_and_stat(tmp_path, monkeypatch):
    """A file deleted between scandir() and .stat() must not crash the whole index.

    Claude Code actively rotates transcripts in these directories, and this
    function runs unattended on a 5-minute scheduler — an unhandled OSError
    here would silently kill the reconcile task.
    """
    proj_a = _FakeEntry("proj_a", str(tmp_path / "proj_a"), is_dir=True)
    proj_b = _FakeEntry("proj_b", str(tmp_path / "proj_b"), is_dir=True)
    vanished = _FakeEntry("gone.jsonl", "gone.jsonl", is_file=True, raise_on_stat=True)

    (tmp_path / "proj_b").mkdir()
    _write_jsonl(tmp_path / "proj_b" / "ok.jsonl", [{"cwd": r"C:\ok"}, {"customTitle": "ok"}])

    real_scandir = os.scandir

    def fake_scandir(path):
        path = str(path)
        if path == str(tmp_path):
            return _FakeScandir([proj_a, proj_b])
        if path == proj_a.path:
            return _FakeScandir([vanished])  # this project's only file vanishes
        if path == proj_b.path:
            return real_scandir(path)  # real directory, unaffected
        raise AssertionError(f"unexpected scandir({path!r})")

    monkeypatch.setattr(os, "scandir", fake_scandir)
    idx = transcript_index(tmp_path)

    assert norm(r"C:\ok") in idx, "the unaffected project must still be indexed"
    assert len(idx) == 1, "the vanished-file project must be skipped, not crash the scan"


def test_title_to_cwd_skips_entries_without_a_title(tmp_path):
    d1 = tmp_path / "a"
    d1.mkdir()
    _write_jsonl(d1 / "a.jsonl", [{"cwd": r"C:\a"}, {"customTitle": "alpha"}])
    d2 = tmp_path / "b"
    d2.mkdir()
    _write_jsonl(d2 / "b.jsonl", [{"cwd": r"C:\b"}])
    mapping = title_to_cwd(transcript_index(tmp_path))
    assert mapping["alpha"] == r"C:\a"
    assert "" not in mapping
