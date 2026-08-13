from __future__ import annotations

import json
import os
import time
import types

from reloaded.discover import (
    TranscriptInfo,
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
    assert strip_glyph("⠐ sample-repo") == "sample-repo"
    assert strip_glyph("⠂ demo-app") == "demo-app"
    assert strip_glyph("plain") == "plain"


def test_strip_glyph_removes_every_shipped_spinner_family():
    """One case per range in _SPINNER_RANGES. Add a case when you add a range.

    An explicit frame list covered only ✳ and the Braille dots, so when Claude
    Code moved to circled halves a busy tab kept its glyph, stopped matching
    its repo basename, and `capture` dropped it - silently, because the tests
    only exercised the frames the list already had.
    """
    for frame in "✳⠐⠂⠁◐◑◒◓":
        assert strip_glyph(f"{frame} sample-repo") == "sample-repo"


def test_strip_glyph_removes_an_unshipped_frame_within_a_known_family():
    # ◴ (U+25F4) is not a frame anything ships today. Ranges rather than
    # literal codepoints are what keep this passing.
    assert strip_glyph("◴ sample-repo") == "sample-repo"


def test_strip_glyph_consumes_modifiers_trailing_a_spinner():
    """VS16, ZWJ and skin-tone modifiers are Mn/Cf/Sk, never So.

    A category-So match left them behind, so "✳️ x" kept an orphan U+FE0F that
    then matched no path at all.
    """
    assert strip_glyph("✳️ sample-repo") == "sample-repo"


def test_strip_glyph_leaves_user_decoration_alone():
    """A renamed non-Claude tab must not resolve to a repo.

    Stripping any symbol cleaned "▶ build" to "build", which resolved against
    a live repo of that name - so `down` typed /exit into the user's own shell
    and could close its window, violating teardown's contract that a window
    with an unrelated tab is left open.
    """
    for title in ("▶ build", "■ stop", "→ deploy", "⚙️ my-repo", "👍🏻 repo"):
        assert strip_glyph(title) == title, f"{title!r} was altered"


def test_strip_glyph_keeps_an_emoji_title_stable_while_busy():
    """The idle and busy forms of one tab must clean to the same key.

    An emoji title is itself a symbol, so a category match erased it: idle
    "🚀" and busy "✳ 🚀" produced different keys and the busy tab vanished.
    """
    assert strip_glyph("🚀") == "🚀"
    assert strip_glyph("✳ 🚀") == strip_glyph("🚀")


def test_strip_glyph_keeps_titles_that_merely_start_with_punctuation():
    assert strip_glyph("-dash-lead") == "-dash-lead"
    assert strip_glyph("_underscore") == "_underscore"
    assert strip_glyph("2fa-service") == "2fa-service"


def test_read_transcript_info_ignores_non_string_fields(tmp_path):
    """A transcript record is untrusted input.

    cwd reaches os.path via paths.norm and customTitle reaches strip_glyph;
    both raise on a non-string. One record with a numeric title would take
    down capture and every reconcile run until that transcript rotated away.
    """
    for junk in (123, True, ["a"], {"a": 1}, None):
        p = tmp_path / f"t{abs(hash(repr(junk)))}.jsonl"
        _write_jsonl(p, [{"cwd": str(tmp_path)}, {"customTitle": junk}])
        info = read_transcript_info(str(p), need_title=True)
        assert info is not None, f"{junk!r} lost the whole record"
        assert info.title == "", f"{junk!r} leaked into title"
        assert title_to_cwd({"k": info}) == {}

    # A non-string cwd is not usable at all, so the record is skipped.
    p = tmp_path / "badcwd.jsonl"
    _write_jsonl(p, [{"cwd": 42}, {"customTitle": "fine"}])
    assert read_transcript_info(str(p), need_title=True) is None


def test_title_to_cwd_keys_stay_injective_on_decorated_titles():
    """Two decorated titles must not collapse onto one key.

    Keying this map on the stripped title makes "✳ api" and "🔧 api" the same
    key; the loser's cwd is discarded, so a live repo vanishes from the layout
    and a tab can be bound to another session's pid.
    """
    index = {
        "a": TranscriptInfo(
            cwd="C:\\repos\\api", title="✳ api", path="p1", mtime=1.0, size=1
        ),
        "b": TranscriptInfo(
            cwd="C:\\repos\\tools", title="🔧 api", path="p2", mtime=2.0, size=1
        ),
    }
    mapping = title_to_cwd(index)
    assert len(mapping) == 2, f"decorated titles collided: {mapping}"
    assert set(mapping.values()) == {"C:\\repos\\api", "C:\\repos\\tools"}


def test_strip_glyph_does_not_keep_the_spinner_on_an_all_symbol_title():
    """A busy tab must clean to the same key as its idle self.

    Falling back to the whole string when the strip empties it returns
    "✳ 🚀" *with* the spinner, so the busy tab stops matching the idle key -
    the exact silent-drop this function exists to prevent. A bare spinner must
    also stay empty, or tabs.list_tab_items counts it and WindowPlan.total_tabs
    never lets `down` close the window.
    """
    assert strip_glyph("✳ 🚀") == strip_glyph("🚀")
    assert strip_glyph("✳") == ""
    assert strip_glyph("⠐ ") == ""
    assert strip_glyph("") == ""
    assert strip_glyph("   ") == ""


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
