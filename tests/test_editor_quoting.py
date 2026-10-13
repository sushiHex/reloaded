"""The editor takes a path with spaces in it (#7).

Every line was split with `raw.split()`, so `a 1 "C:\\work repos\\client"` was
four tokens, matched no command and fell through as unrecognized - and the
documentation's answer was to hand-edit the layout JSON instead.

Quotes group and nothing escapes. shlex and CommandLineToArgvW both read `\\"`
as an escaped quote, which is exactly how a Windows path with a trailing
separator ends.
"""
from __future__ import annotations

import copy

import pytest

import reloaded.editor as editor_mod
from conftest import make_layout, make_window
from reloaded.layout import Tab

SPACED = r"C:\work repos\client"


@pytest.fixture
def editor(monkeypatch, tmp_path):
    """Run the editor over a two-tab window; returns (run, the layout, adds)."""
    lo = make_layout([make_window([0, 0, 800, 600],
                                  [Tab(cwd=r"C:\repos\one", title="one"),
                                   Tab(cwd=r"C:\repos\two", title="two")])])
    dirs = []
    monkeypatch.setattr(editor_mod.os.path, "isdir",
                        lambda p: dirs.append(p) or True)
    monkeypatch.setattr(editor_mod, "_live_agent", lambda cwd: ("claude", ""))

    prompts = []

    def run(*commands):
        typed = iter(list(commands) + ["q", "y"])
        monkeypatch.setattr("builtins.input",
                            lambda prompt="": prompts.append(prompt) or next(typed))
        editor_mod.run(lo, tmp_path / "default.json", r"C:\repos",
                       editor_mod.layout_mod.save)
        return lo

    run.prompts = prompts
    return run, lo, dirs


@pytest.mark.parametrize("raw, tokens", [
    ("a 1 thing", ["a", "1", "thing"]),
    ("  m   1.2\t1  ", ["m", "1.2", "1"]),
    (f'a 1 "{SPACED}"', ["a", "1", SPACED]),
    ('a 1 "C:\\work repos\\client\\"', ["a", "1", "C:\\work repos\\client\\"]),
    ('a 1 C:\\"work repos"\\client', ["a", "1", SPACED]),
    ('a 1 ""', ["a", "1", ""]),
    ('a 1 "foo""bar"', ["a", "1", "foobar"]),
    ("", []),
])
def test_the_split(raw, tokens):
    assert editor_mod.split_input(raw) == tokens


def test_an_unterminated_quote_is_an_error():
    with pytest.raises(ValueError, match="unterminated"):
        editor_mod.split_input('a 1 "C:\\work repos')


def test_a_quoted_path_with_spaces_is_added(editor):
    run, _lo, dirs = editor
    lo = run(f'a 1 "{SPACED}"')

    added = lo.windows[0].tabs[-1]
    assert added.cwd == SPACED
    assert added.title == "client"
    assert added.pinned
    assert dirs == [SPACED]


def test_a_trailing_separator_reaches_the_resolver_intact(editor):
    run, _lo, dirs = editor
    run('a 1 "C:\\work repos\\client\\"')

    assert dirs == ["C:\\work repos\\client\\"]


def test_unquoted_commands_behave_as_before(editor):
    run, _lo, _dirs = editor
    lo = run("o 1.2 1", "a 1 three", "d 1.1")

    assert [t.title for t in lo.windows[0].tabs] == ["one", "three"]


def test_a_move_still_works(editor):
    run, _lo, _dirs = editor
    lo = run("n", "m 1.2 2")

    assert [[t.title for t in w.tabs] for w in lo.windows] == [["one"], ["two"]]


def test_malformed_quoting_changes_nothing(editor, capsys):
    run, lo, dirs = editor
    before = copy.deepcopy(lo)

    run('a 1 "C:\\work repos')

    assert "unterminated quote" in capsys.readouterr().out
    assert dirs == [], "it resolved a path from a line it could not parse"
    assert lo == before
    # Quitting did not ask about unsaved changes: nothing was marked dirty.
    assert not any("discard" in p for p in run.prompts)


def test_malformed_quoting_keeps_the_editor_open(editor):
    run, _lo, _dirs = editor
    lo = run('a 1 "C:\\work repos', "o 1.2 1")

    assert [t.title for t in lo.windows[0].tabs] == ["two", "one"]


@pytest.mark.parametrize("arg", ['""', '"  "'])
def test_an_empty_quoted_repo_is_refused(editor, capsys, arg):
    """Resolved, an empty name is the repos root itself - which exists, so
    without this it would be added and pinned as a tab."""
    run, _lo, dirs = editor
    lo = run(f"a 1 {arg}")

    assert "no repo given" in capsys.readouterr().out
    assert dirs == []
    assert [t.title for t in lo.windows[0].tabs] == ["one", "two"]
