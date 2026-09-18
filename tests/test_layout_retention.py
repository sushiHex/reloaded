"""A capture that loses repos keeps the layout it replaced.

The reconcile task rewrites the layout every five minutes, forever, under
`pyw.exe` so nothing it prints has anywhere to go. There is one copy of the
file and no history, so a capture that drops a repo is both silent and final —
which is how a reboot took sessions out of a layout with nothing left to say
what had been there.

Rotation is keyed on loss, not on every write, and that is the whole design.
A `.prev` refreshed on every save is overwritten ninety times between a 04:40
problem and noticing it at noon; one that only moves when repos disappear still
points at the last layout that had them.

Recovery needs no new flag: `layouts/default.prev.json` is what `--layout
default.prev` already resolves to.
"""
from __future__ import annotations

import pytest

from conftest import make_layout, make_window
from reloaded import layout as layout_mod
from reloaded.layout import Tab
from reloaded.paths import layout_path


def _layout(*repos):
    return make_layout([make_window(
        [0, 0, 800, 600],
        [Tab(cwd=rf"C:\repos\{r}", title=r) for r in repos],
    )])


def _saved(path):
    return {t.title for w in layout_mod.load(path).windows for t in w.tabs}


def test_losing_a_repo_keeps_the_layout_that_had_it(tmp_path):
    path = tmp_path / "default.json"
    layout_mod.save(_layout("app", "beta"), path)

    layout_mod.save(_layout("app"), path)

    assert _saved(tmp_path / "default.prev.json") == {"app", "beta"}
    assert _saved(path) == {"app"}


def test_save_reports_where_it_put_the_previous_layout(tmp_path):
    """The caller is the one that can say so out loud; this is how it knows."""
    path = tmp_path / "default.json"
    layout_mod.save(_layout("app", "beta"), path)

    rotated = layout_mod.save(_layout("app"), path)

    assert rotated == tmp_path / "default.prev.json"


def test_moving_a_window_does_not_burn_the_backup(tmp_path):
    """Geometry churn is the common case — the reconcile writes one every five
    minutes. Rotating on every save would keep `.prev` five minutes old, which
    is exactly as useless as having none when the loss is noticed hours later.
    """
    path = tmp_path / "default.json"
    layout_mod.save(_layout("app", "beta"), path)

    moved = make_layout([make_window(
        [500, 500, 800, 600],
        [Tab(cwd=r"C:\repos\app", title="app"), Tab(cwd=r"C:\repos\beta", title="beta")],
    )])
    rotated = layout_mod.save(moved, path)

    assert rotated is None
    assert not (tmp_path / "default.prev.json").exists()


def test_gaining_a_repo_does_not_rotate(tmp_path):
    path = tmp_path / "default.json"
    layout_mod.save(_layout("app"), path)

    rotated = layout_mod.save(_layout("app", "beta"), path)

    assert rotated is None


def test_the_first_save_has_nothing_to_keep(tmp_path):
    path = tmp_path / "default.json"

    rotated = layout_mod.save(_layout("app"), path)

    assert rotated is None
    assert not (tmp_path / "default.prev.json").exists()


def test_an_unreadable_previous_layout_does_not_stop_the_save(tmp_path):
    """The rotation is a courtesy. A layout that cannot be parsed has nothing
    worth preserving, and refusing to save over it would wedge the reconcile
    against a file only a human can clear."""
    path = tmp_path / "default.json"
    path.write_text("{not json", encoding="utf-8")

    rotated = layout_mod.save(_layout("app"), path)

    assert rotated is None
    assert _saved(path) == {"app"}


def test_the_kept_layout_is_a_layout(tmp_path):
    """Not a fragment, not the temp file — something `up` could deploy."""
    path = tmp_path / "default.json"
    layout_mod.save(_layout("app", "beta"), path)
    layout_mod.save(_layout("app"), path)

    kept = layout_mod.load(tmp_path / "default.prev.json")

    assert kept.version == layout_mod.LAYOUT_VERSION
    assert [t.cwd for w in kept.windows for t in w.tabs] == [
        r"C:\repos\app", r"C:\repos\beta"]


def test_the_kept_layout_is_addressable_as_a_layout_name(monkeypatch, tmp_path):
    """`--layout default.prev` resolves to the rotated file, so recovery is
    `reloaded --layout default.prev up` with no new flag to add, document, or
    test against the README table."""
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr("os.path.expanduser", lambda p: p.replace("~", str(tmp_path)))

    assert layout_path("default.prev").name == "default.prev.json"
    assert layout_path("default.prev").parent == layout_path("default").parent


def test_a_second_loss_does_not_destroy_the_copy_from_the_first(tmp_path):
    """The cascade this feature is useless without.

    A reboot does not lose sessions all at once. Tabs come up on a four-second
    stagger, agents fail one at a time, and the reconcile fires every five
    minutes through the whole thing. Rotating on each loss walks the copy
    forward with the damage - by the third capture `.prev` holds a layout
    nearly as degraded as the live one, and a user who trusts it gets back
    something that is already missing most of what they wanted.

    A burst of losses is one event. The state worth keeping is the one from
    before it started, so the copy holds still while the damage is ongoing.
    """
    path = tmp_path / "default.json"
    layout_mod.save(_layout("app", "beta", "gamma"), path)
    layout_mod.save(_layout("app", "beta"), path)

    layout_mod.save(_layout("app"), path)

    assert _saved(tmp_path / "default.prev.json") == {"app", "beta", "gamma"}


def test_a_held_copy_is_still_reported_to_the_caller(tmp_path):
    """Declining to overwrite is not "there is no backup" - it is "the backup
    is the good one". A caller told None would log a loss with no way back."""
    path = tmp_path / "default.json"
    layout_mod.save(_layout("app", "beta", "gamma"), path)
    layout_mod.save(_layout("app", "beta"), path)

    kept = layout_mod.save(_layout("app"), path)

    assert kept == tmp_path / "default.prev.json"


def test_a_cold_copy_is_replaced(tmp_path):
    """The hold is a burst window, not a permanent freeze. A loss long after
    the last one is a new event, and the layout just before it is now the
    interesting one."""
    import os
    import time

    path = tmp_path / "default.json"
    layout_mod.save(_layout("app", "beta", "gamma"), path)
    layout_mod.save(_layout("app", "beta"), path)
    prev = tmp_path / "default.prev.json"
    old = time.time() - (layout_mod.PREVIOUS_HOLD_SECONDS + 60)
    os.utime(prev, (old, old))

    layout_mod.save(_layout("app"), path)

    assert _saved(prev) == {"app", "beta"}


def test_only_the_repos_decide_a_rotation(tmp_path):
    """Two tabs for one repo, then one: the repo set is unchanged, so nothing
    was lost. Deduping is build_layout's job and a rotation must not second
    guess it."""
    path = tmp_path / "default.json"
    layout_mod.save(make_layout([make_window(
        [0, 0, 800, 600],
        [Tab(cwd=r"C:\repos\app", title="app"), Tab(cwd=r"C:\repos\app", title="app")],
    )]), path)

    rotated = layout_mod.save(_layout("app"), path)

    assert rotated is None
