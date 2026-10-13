"""A `/relaunch` that failed is reported to the next one in its directory.

Once its session is gone, the helper has nobody to tell but the log, and
nobody reads that unprompted. So every attempt leaves a record, and the next
`/relaunch` in that directory says what became of any it finds.

Issue #36 recorded seven ways an earlier version got this wrong; these tests
are named for them. The record is written by the dispatching process before
the spawn (a helper that died never writes one), is one file per attempt (two
overlapping attempts cannot settle each other), and is claimed by renaming it
rather than read and then deleted. Whether its helper is still working is
answered by Windows, not by arithmetic: the record is held open by every
process in the attempt from creation on, and a file held that way cannot be
renamed. The holds here are real file handles, in this process.
"""
from __future__ import annotations

import datetime
import types

import pytest

import reloaded.paths as paths_mod
import reloaded.relaunch as relaunch_mod

CWD = r"C:\repos\app"
WHEN = datetime.datetime(2026, 10, 13, 14, 2, 11)


def _record(cwd=CWD, at=WHEN, outcome=None):
    path = paths_mod.relaunch_attempt(cwd, at.timestamp())
    path.write_text(outcome or "", encoding="utf-8")
    return path


def _report(cwd=CWD):
    said = []
    relaunch_mod.report_earlier(cwd, said.append)
    return said


# ── reading what an earlier attempt left ─────────────────────────────────


def test_a_helper_that_never_reported_back_is_reported_with_its_time():
    """The seventh defect: a record that grew a second field made every
    timestamp render as "an unknown time", and a test that only checked the
    message was present could not tell. This one reads the time."""
    _record()

    said = _report()

    assert len(said) == 1
    assert "at 2026-10-13 14:02:11 never reported back" in said[0]
    assert paths_mod.relaunch_attempts(CWD) == [], "reported, and kept"


def test_a_failure_the_helper_recorded_is_reported_in_its_words():
    _record(outcome="failed\npid 20 ended, but its shell (pid 10) went with it\n")

    said = _report()

    assert said == ["[relaunch] The /relaunch here at 2026-10-13 14:02:11 did "
                    "not bring its session back: pid 20 ended, but its shell "
                    "(pid 10) went with it"]


def test_a_restart_called_off_is_reported_as_that():
    """Not as a session lost: it was left running."""
    _record(outcome="cancelled\nthe restart was called off - left running\n")

    (said,) = _report()

    assert "was called off" in said
    assert "did not bring" not in said


def test_one_that_worked_is_cleared_without_a_word():
    _record(outcome="ok\npid 20 ended; its tab's loop is bringing it back\n")

    assert _report() == []
    assert paths_mod.relaunch_attempts(CWD) == []


def test_a_record_still_held_is_an_attempt_still_running():
    """The fourth defect: repeating `/relaunch` moments later finds one that
    has neither failed nor finished. Reporting it is wrong, and consuming it
    destroys the evidence. Held is the helper's real state - the sixth."""
    path = _record()
    with open(path, encoding="utf-8"):
        assert _report() == []
        assert paths_mod.relaunch_attempts(CWD) == [path]
    assert len(_report()) == 1, "once it lets go, it is reported"


def test_overlapping_attempts_are_settled_separately():
    """The second defect: keyed by directory alone, the older helper settled
    the newer one's record. Here the newer one is still running."""
    older = _record(at=WHEN)
    newer = _record(at=WHEN + datetime.timedelta(seconds=5))
    with open(newer, encoding="utf-8"):
        said = _report()
    assert len(said) == 1 and "14:02:11" in said[0]
    assert paths_mod.relaunch_attempts(CWD) == [newer]
    assert not older.exists()


def test_several_are_reported_oldest_first():
    _record(at=WHEN + datetime.timedelta(minutes=1), outcome="failed\nsecond\n")
    _record(at=WHEN, outcome="failed\nfirst\n")

    said = _report()

    assert [s.rsplit(": ", 1)[-1] for s in said] == ["first", "second"]


def test_another_directorys_attempts_are_not_this_ones():
    _record(cwd=r"C:\repos\other")

    assert _report() == []
    assert len(paths_mod.relaunch_attempts(r"C:\repos\other")) == 1


def test_the_directory_is_matched_however_it_is_spelled():
    _record(cwd=r"C:\Repos\App")

    assert len(_report(r"c:\repos\app\.")) == 1


def test_a_claim_that_was_never_finished_is_reported_next_time():
    """A reader that died between claiming and saying would otherwise lose
    the report for good. Saying it twice is the better failure."""
    path = _record(outcome="failed\nit broke\n")
    stranded = path.with_name(".".join(path.name.split(".")[:3]
                                       + ["dead", "claimed"]))
    path.rename(stranded)

    (said,) = _report()

    assert "14:02:11 did not bring its session back: it broke" in said


def test_a_dry_run_reads_nothing():
    """A dry run changes nothing, and consuming a report would."""
    _record()

    relaunch_mod.relaunch(99999999, CWD, "claude", dry_run=True,
                          say=lambda *_: None)

    assert len(paths_mod.relaunch_attempts(CWD)) == 1


# ── what the dispatcher writes ───────────────────────────────────────────


class _Proc:
    def __init__(self, pid, cwd=CWD):
        self.pid = pid
        self._cwd = cwd

    def cwd(self):
        if self._cwd is None:
            raise OSError("access denied")
        return self._cwd

    def create_time(self):
        return 100.0


class _Started:
    def __init__(self, returncode=0):
        self.returncode = returncode

    def wait(self, timeout=None):
        return self.returncode


@pytest.fixture
def spawn(monkeypatch, tmp_path):
    """_spawn_helper with Popen faked. Each call records what was true at the
    moment the process would have been created."""
    seen = []

    def _popen(argv, startupinfo=None, **kw):
        records = paths_mod.relaunch_attempts(CWD)
        held = False
        if records:
            try:
                records[0].rename(records[0])
            except OSError:
                held = True
        seen.append(types.SimpleNamespace(
            records=records, held=held, argv=argv,
            handles=(startupinfo.lpAttributeList["handle_list"]
                     if startupinfo else None)))
        return _Started(state["code"])

    state = {"code": 0}
    monkeypatch.setattr(relaunch_mod.subprocess, "Popen", _popen)
    monkeypatch.setattr(relaunch_mod, "log_path", lambda: tmp_path / "log")

    def _run(session=None):
        relaunch_mod._spawn_helper(session or _Proc(20), _Proc(10), "claude",
                                   "claude --resume id", tmp_path / "m")

    return types.SimpleNamespace(run=_run, seen=seen, state=state)


def test_the_record_exists_before_anything_is_started(spawn):
    """The first defect: a record only a helper that lived could write would
    never report one that died."""
    spawn.run()

    (call,) = spawn.seen
    assert len(call.records) == 1
    assert call.held, "unheld, a reader could take it before the helper exists"


def test_the_intermediate_inherits_the_hold_and_nothing_else(spawn):
    spawn.run()

    (call,) = spawn.seen
    assert call.handles and len(call.handles) == 1
    assert f"_start_helper(*({call.handles[0]}," in call.argv[-1]
    assert repr(str(call.records[0])) in call.argv[-1], "the helper is told its record"


def test_the_dispatcher_lets_go_once_the_intermediate_is_gone(spawn):
    """From here on the hold is the children's: held by this process too, a
    dispatcher that outlived a dead helper would hide it."""
    spawn.run()

    (record,) = paths_mod.relaunch_attempts(CWD)
    record.rename(record)  # raises if still held


def test_a_launch_that_failed_leaves_no_record(spawn):
    """The caller says so, then and there - and nothing holds the record, so
    no helper was started to say anything later."""
    spawn.state["code"] = 1

    with pytest.raises(OSError):
        spawn.run()

    assert paths_mod.relaunch_attempts(CWD) == []


def test_an_unreadable_directory_still_starts_the_helper(spawn):
    """There is nowhere to file the record, and that is no reason to refuse
    the restart."""
    spawn.run(_Proc(20, cwd=None))

    (call,) = spawn.seen
    assert call.handles is None
    assert "_start_helper(*(None," in call.argv[-1]


def test_the_intermediate_hands_the_hold_on_to_the_helper(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(relaunch_mod.subprocess, "Popen",
                        lambda argv, startupinfo=None, **kw: seen.append(
                            (argv, startupinfo.lpAttributeList["handle_list"])))
    monkeypatch.setattr(relaunch_mod, "log_path", lambda: tmp_path / "log")

    relaunch_mod._start_helper(1234, 20, 1.0, 10, 1.0, "claude", "c", "m",
                               False, "rec")

    ((argv, handles),) = seen
    assert handles == [1234]
    assert "bring_back(*(20," in argv[-1] and "'rec')" in argv[-1]


# ── what the helper writes ───────────────────────────────────────────────


@pytest.fixture
def helper(monkeypatch):
    """bring_back with its work replaced by a chosen ending."""
    ending = {}

    def _work(*args):
        if "raise" in ending:
            raise ending["raise"]
        return ending["status"], ending["said"]

    monkeypatch.setattr(relaunch_mod, "_bring_back", _work)

    def _run(record):
        relaunch_mod.bring_back(20, 1.0, 10, 1.0, "claude", "c", "m", False,
                                str(record))

    return types.SimpleNamespace(ending=ending, run=_run)


@pytest.mark.parametrize("status", ["ok", "failed", "cancelled"])
def test_the_helper_records_how_it_ended(helper, status):
    record = _record()
    helper.ending.update(status=status, said="what happened")

    helper.run(record)

    assert record.read_text(encoding="utf-8") == f"{status}\nwhat happened\n"


def test_a_helper_that_crashes_records_that_and_still_raises(helper):
    record = _record()
    helper.ending["raise"] = RuntimeError("boom")

    with pytest.raises(RuntimeError):
        helper.run(record)

    status, said = record.read_text(encoding="utf-8").split("\n", 1)
    assert status == "failed" and "boom" in said


def test_end_to_end_a_failure_reaches_the_next_relaunch(helper):
    record = _record()
    helper.ending.update(status="failed", said="could not write to shell pid 10")

    helper.run(record)

    (said,) = _report()
    assert said.endswith("did not bring its session back: could not write to "
                         "shell pid 10")
