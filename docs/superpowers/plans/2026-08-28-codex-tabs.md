# Codex CLI Tab Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach `reloaded` that a managed tab may be a Codex CLI session as well as a Claude Code one, with full parity across discovery, capture, `up`, `status`, `down` and `restart`.

**Architecture:** A new `reloaded/agents.py` holds the only things that differ per kind — process name, binary, launch command, quit keystrokes, sessions directory. `Tab` gains two optional fields (`agent`, `command`) so a layout without them still reads as Claude. Everything upstream of that (geometry, placement, the restart-marker loop, staggering) is already kind-agnostic and is not touched.

**Tech Stack:** Python 3.9+, stdlib only plus `psutil` and `uiautomation`. Windows-only. pytest.

**Spec:** `docs/superpowers/specs/2026-08-28-codex-tabs-design.md`

## Global Constraints

- Python 3.9+ compatible. No third-party deps beyond `psutil` and `uiautomation`, both already used.
- Tests monkeypatch UIA and the live-session scan at the module boundary, matching `tests/test_teardown.py`.
- Assert on behaviour, never on mock call arguments alone.
- Every task ends green: `python -m pytest -q` passes before commit.
- `live_sessions() -> dict[str, int]` keeps its signature. Nine call sites depend on it; kind travels in a parallel map instead. **This refines the spec**, which proposed changing the return type.
- A caller that cannot confirm which tab it is typing into must not type.
- Codex quit keys were verified on a disposable session: `Ctrl+C` twice.

---

### Task 1: `select_tab` verifies the tab, not just the window

Fixes the bug behind every unexplained `/exit` failure. Ships alone and is worth having with no Codex work at all.

**Files:**
- Modify: `reloaded/tabs.py:150-178`
- Test: `tests/test_tabs_select.py`

**Interfaces:**
- Produces: `select_tab(hwnd: int, tab_item, attempts: int = 10, settle: float = 0.3) -> bool` — now `False` when the tab never becomes selected.

- [ ] **Step 1: Write the failing tests**

```python
"""select_tab must confirm the TAB took focus, not just the window."""
from __future__ import annotations

import pytest

import reloaded.tabs as tabs_mod


class _Pattern:
    def __init__(self, takes_after: int | None):
        self._takes_after = takes_after
        self.selects = 0
        self.IsSelected = False

    def Select(self):
        self.selects += 1
        if self._takes_after is not None and self.selects >= self._takes_after:
            self.IsSelected = True


class _Item:
    def __init__(self, takes_after=1, raises=False):
        self._pattern = _Pattern(takes_after)
        self._raises = raises

    def GetSelectionItemPattern(self):
        if self._raises:
            raise RuntimeError("no selection pattern")
        return self._pattern


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(tabs_mod.time, "sleep", lambda s: None, raising=False)
    monkeypatch.setattr("time.sleep", lambda s: None)


@pytest.fixture
def foreground(monkeypatch):
    monkeypatch.setattr(tabs_mod.win32, "set_foreground", lambda hwnd: True)


def test_a_tab_that_selects_immediately_succeeds(foreground):
    assert tabs_mod.select_tab(1, _Item(takes_after=1)) is True


def test_a_tab_that_selects_on_a_later_attempt_succeeds(foreground):
    item = _Item(takes_after=3)
    assert tabs_mod.select_tab(1, item) is True
    assert item._pattern.selects >= 3


def test_a_tab_that_never_selects_fails(foreground):
    """The bug. Returning True here sends keystrokes to whichever tab IS
    active - which is how /exit reached the wrong session for two days."""
    assert tabs_mod.select_tab(1, _Item(takes_after=None)) is False


def test_a_tab_whose_pattern_raises_fails(foreground):
    assert tabs_mod.select_tab(1, _Item(raises=True)) is False


def test_a_window_that_will_not_foreground_fails(monkeypatch):
    monkeypatch.setattr(tabs_mod.win32, "set_foreground", lambda hwnd: False)
    assert tabs_mod.select_tab(1, _Item(takes_after=1)) is False


def test_selection_is_not_retried_once_it_has_taken(foreground):
    item = _Item(takes_after=1)
    tabs_mod.select_tab(1, item)
    assert item._pattern.selects == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_tabs_select.py -q`
Expected: FAIL — `test_a_tab_that_never_selects_fails` returns True, and `test_a_tab_whose_pattern_raises_fails` returns True.

- [ ] **Step 3: Replace `select_tab`**

```python
def select_tab(hwnd: int, tab_item, attempts: int = 10, settle: float = 0.3) -> bool:
    """Select `tab_item`, confirm it actually took, and foreground its window.

    Returns False unless BOTH are true, because SendKeys goes to whatever has
    OS focus rather than to whatever UIA element was asked for.

    Confirming the tab is the whole point. Select() used to be best-effort and
    swallowed, with only the window's foreground state checked - so a silently
    failed Select() sent every keystroke to whichever tab was already active.
    Measured: a tab reported selected=False immediately after this returned
    True, and the same {Enter} that had done nothing three times landed as
    soon as selection was verified.
    """
    import time

    for attempt in range(attempts):
        try:
            pattern = tab_item.GetSelectionItemPattern()
            if pattern.IsSelected:
                break
            pattern.Select()
        except Exception:
            # No selection pattern at all: nothing here can be confirmed, and
            # typing blind is what this function exists to prevent.
            return False
        time.sleep(settle)
    else:
        return False

    return win32.set_foreground(hwnd)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_tabs_select.py tests/test_tabs.py tests/test_teardown.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_tabs_select.py reloaded/tabs.py
git commit -m "fix(tabs): confirm the tab took focus, not just the window"
```

---

### Task 2: close a tab by invoking its own button

**Files:**
- Modify: `reloaded/tabs.py` (append)
- Test: `tests/test_tabs_close.py`

**Interfaces:**
- Produces: `close_tab(tab_item) -> bool` — invokes the tab's `Close Tab` button. No focus, no foreground, no keystroke.

- [ ] **Step 1: Write the failing tests**

```python
"""Closing a tab needs no keystroke, so it cannot hit the wrong tab."""
from __future__ import annotations

import reloaded.tabs as tabs_mod


class _Invoke:
    def __init__(self):
        self.invoked = 0

    def Invoke(self):
        self.invoked += 1


class _Button:
    def __init__(self, name="Close Tab", raises=False):
        self.Name = name
        self.ControlTypeName = "ButtonControl"
        self._pattern = _Invoke()
        self._raises = raises

    def GetInvokePattern(self):
        if self._raises:
            raise RuntimeError("no invoke pattern")
        return self._pattern


class _Text:
    Name = "some label"
    ControlTypeName = "TextControl"


class _Item:
    def __init__(self, *children):
        self._children = list(children)

    def GetChildren(self):
        return list(self._children)


def test_the_close_button_is_invoked():
    button = _Button()
    assert tabs_mod.close_tab(_Item(_Text(), button)) is True
    assert button._pattern.invoked == 1


def test_a_tab_with_no_close_button_reports_failure():
    assert tabs_mod.close_tab(_Item(_Text())) is False


def test_a_button_that_cannot_be_invoked_reports_failure():
    assert tabs_mod.close_tab(_Item(_Button(raises=True))) is False


def test_a_non_button_named_close_is_not_invoked():
    """Matching on the name alone would fire whatever happened to be called
    that - a label, a menu item."""
    text = _Text()
    text.Name = "Close Tab"
    assert tabs_mod.close_tab(_Item(text)) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_tabs_close.py -q`
Expected: FAIL with `AttributeError: module 'reloaded.tabs' has no attribute 'close_tab'`

- [ ] **Step 3: Add `close_tab`**

```python
def close_tab(tab_item) -> bool:
    """Close one tab by invoking its own Close Tab button.

    Needs no focus, no foreground and no keystroke, so unlike send_exit_keystrokes
    it cannot land on the wrong tab. Verified against a wedged tab that Ctrl+D
    would not close, with three live sessions in the same window untouched.

    For a tab that must be GONE and whose session has already ended. A live
    session is still asked to quit through its own UI, so it gets the chance to
    shut down cleanly rather than having its tab pulled from under it.
    """
    for child in tab_item.GetChildren():
        if child.ControlTypeName != "ButtonControl":
            continue
        if "close" not in (child.Name or "").lower():
            continue
        try:
            child.GetInvokePattern().Invoke()
            return True
        except Exception:
            return False
    return False
```

- [ ] **Step 4: Write the failing test for its consumer**

`close_tab` must not ship without one. A session that ends in a shell which
does not close itself leaves a dead tab behind — seen for real: a
hand-launched session exited and left a bare prompt sitting in the strip.

Append to `tests/test_tabs_close.py`:

```python
import reloaded.teardown as teardown_mod


def test_a_tab_whose_session_ended_but_did_not_close_is_closed():
    """The window survives, so WM_CLOSE is wrong; only this tab must go."""
    button = _Button()
    item = _Item(button)
    plan = teardown_mod.WindowPlan(
        hwnd=1, total_tabs=2, targets=[("alpha", r"C:\repos\alpha", 111, item)]
    )

    teardown_mod.close_dead_tabs([plan], exited=[("alpha", r"C:\repos\alpha")],
                                 still_open=lambda it: True)

    assert button._pattern.invoked == 1


def test_a_tab_that_closed_itself_is_left_alone():
    """Its shell ran `exit` and the tab is already gone; invoking a button on
    a dead UIA element is a needless exception."""
    button = _Button()
    item = _Item(button)
    plan = teardown_mod.WindowPlan(
        hwnd=1, total_tabs=2, targets=[("alpha", r"C:\repos\alpha", 111, item)]
    )

    teardown_mod.close_dead_tabs([plan], exited=[("alpha", r"C:\repos\alpha")],
                                 still_open=lambda it: False)

    assert button._pattern.invoked == 0


def test_a_session_that_never_exited_keeps_its_tab():
    """It is still running. Closing its tab would kill it."""
    button = _Button()
    item = _Item(button)
    plan = teardown_mod.WindowPlan(
        hwnd=1, total_tabs=2, targets=[("alpha", r"C:\repos\alpha", 111, item)]
    )

    teardown_mod.close_dead_tabs([plan], exited=[], still_open=lambda it: True)

    assert button._pattern.invoked == 0
```

- [ ] **Step 5: Add the consumer**

In `reloaded/teardown.py`:

```python
def close_dead_tabs(plans, exited, still_open=None, log=print) -> int:
    """Close tabs whose session ended but whose shell did not close itself.

    A tab launched by reloaded closes itself once its session ends. One started
    by hand sits in a plain interactive shell and does not, leaving a dead tab
    in the strip. WM_CLOSE is the wrong tool - the window may hold other live
    sessions - so each tab is closed through its own button.

    Only tabs in `exited` are touched. A session that never exited is still
    running, and closing its tab would kill it.
    """
    from . import tabs as tabs_mod

    ended = {cwd for _title, cwd in exited}
    closed = 0
    for plan in plans:
        for title, cwd, _pid, item in plan.targets:
            if cwd not in ended:
                continue
            if still_open is not None and not still_open(item):
                continue
            if tabs_mod.close_tab(item):
                log(f"    closed the empty tab left by {title}")
                closed += 1
    return closed
```

Call it from `execute_down` after the exit loop, before the window-close
decision, and subtract closed tabs from the window's remaining count.

- [ ] **Step 6: Run to verify it passes**

Run: `python -m pytest tests/test_tabs_close.py tests/test_tabs.py tests/test_teardown.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add tests/test_tabs_close.py reloaded/tabs.py reloaded/teardown.py
git commit -m "feat(teardown): close a tab whose session ended but whose shell did not"
```

---

### Task 3: the agent registry

**Files:**
- Create: `reloaded/agents.py`
- Test: `tests/test_agents.py`

**Interfaces:**
- Produces:
  - `CLAUDE: Agent`, `CODEX: Agent`, `AGENTS: dict[str, Agent]`
  - `Agent` fields: `kind: str`, `process: str`, `binary: str`, `launch: str`, `quit_keys: tuple[str, ...]`, `sessions_dir: str`
  - `for_kind(kind: str) -> Agent` — unknown or empty kind returns `CLAUDE`
  - `DEFAULT_KIND = "claude"`

- [ ] **Step 1: Write the failing tests**

```python
"""What differs between agent kinds, and nothing else."""
from __future__ import annotations

import reloaded.agents as agents


def test_both_kinds_are_registered():
    assert set(agents.AGENTS) == {"claude", "codex"}


def test_claude_is_the_default_for_an_unknown_kind():
    """A layout written before kinds existed carries no `agent` field."""
    assert agents.for_kind("").kind == "claude"
    assert agents.for_kind("gemini").kind == "claude"
    assert agents.for_kind(None).kind == "claude"


def test_each_kind_names_its_own_process():
    assert agents.CLAUDE.process == "claude.exe"
    assert agents.CODEX.process == "codex.exe"


def test_each_kind_names_the_binary_readiness_waits_for():
    """Separate from `process`: readiness looks it up on PATH, discovery
    matches a running image name."""
    assert agents.CLAUDE.binary == "claude"
    assert agents.CODEX.binary == "codex"


def test_quit_keys_are_sent_one_at_a_time():
    """A tuple, not a string. Claude Code's slash menu will not accept an
    Enter that arrives ten milliseconds after the command."""
    assert agents.CLAUDE.quit_keys == ("/exit", "{Enter}")
    assert agents.CODEX.quit_keys == ("{Ctrl}c", "{Ctrl}c")


def test_codex_quit_keys_match_what_was_verified():
    """Ctrl+C twice, measured on a disposable session: the target died and
    other sessions were untouched."""
    assert agents.CODEX.quit_keys.count("{Ctrl}c") == 2


def test_each_kind_knows_where_its_sessions_live():
    assert ".claude" in agents.CLAUDE.sessions_dir
    assert ".codex" in agents.CODEX.sessions_dir


def test_an_agent_is_immutable():
    """The registry is read by every module; a mutated field would be a
    spooky action at a distance."""
    import dataclasses
    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        agents.CODEX.launch = "something else"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_agents.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'reloaded.agents'`

- [ ] **Step 3: Write `reloaded/agents.py`**

```python
"""The only things that differ between agent kinds.

Window placement, geometry, the restart-marker loop and the staggered launch
are all kind-agnostic and live elsewhere. Anything that lands here should be
something a second CLI genuinely does differently.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Agent:
    kind: str
    # The running image name, for discovery.
    process: str
    # What readiness waits for on PATH. Not the same thing: `codex.exe` runs,
    # but `codex` is what shutil.which finds.
    binary: str
    # Used only when a tab carries no captured command of its own.
    launch: str
    # Sent one at a time with a pause between. Claude Code's slash-command
    # menu does not accept an Enter arriving right behind the command.
    quit_keys: tuple
    sessions_dir: str


DEFAULT_KIND = "claude"

CLAUDE = Agent(
    kind="claude",
    process="claude.exe",
    binary="claude",
    launch="claude --dangerously-skip-permissions --continue",
    quit_keys=("/exit", "{Enter}"),
    sessions_dir=os.path.join(os.path.expanduser("~"), ".claude", "projects"),
)

CODEX = Agent(
    kind="codex",
    process="codex.exe",
    binary="codex",
    launch="codex resume --last --dangerously-bypass-approvals-and-sandbox",
    # Verified on a disposable session: the target process died, other
    # sessions were untouched. `/quit` did nothing.
    quit_keys=("{Ctrl}c", "{Ctrl}c"),
    sessions_dir=os.path.join(os.path.expanduser("~"), ".codex", "sessions"),
)

AGENTS = {a.kind: a for a in (CLAUDE, CODEX)}


def for_kind(kind) -> Agent:
    """The agent for `kind`, defaulting to Claude Code.

    Defaulting rather than raising is what lets every layout written before
    kinds existed keep working untouched.
    """
    return AGENTS.get(str(kind or "").strip().lower(), CLAUDE)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_agents.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_agents.py reloaded/agents.py
git commit -m "feat(agents): registry of what differs between agent kinds"
```

---

### Task 4: discovery finds Codex sessions

**Files:**
- Modify: `reloaded/discover.py:136` (`live_sessions`), append `live_agents`
- Test: `tests/test_discover_agents.py`

**Interfaces:**
- Consumes: `agents.AGENTS`, `agents.DEFAULT_KIND` (Task 3)
- Produces:
  - `live_sessions() -> dict[str, int]` — **signature unchanged**, now includes Codex sessions
  - `live_agents() -> dict[str, str]` — normalised cwd to kind
- `DESKTOP_HOSTS = ("chatgpt.exe",)`

- [ ] **Step 1: Write the failing tests**

```python
"""Discovery sees both kinds, and excludes the desktop-app Codex."""
from __future__ import annotations

import reloaded.discover as discover_mod
from reloaded.paths import norm

REPOS = r"C:\Users\k\repos"


class _Proc:
    def __init__(self, pid, name, cwd, parent_name="pwsh.exe", ppid=1):
        self.pid = pid
        self.info = {"pid": pid, "name": name, "ppid": ppid}
        self._cwd = cwd
        self._parent_name = parent_name

    def cwd(self):
        if self._cwd is None:
            raise PermissionError("denied")
        return self._cwd

    def name(self):
        return self.info["name"]

    def ppid(self):
        return self.info["ppid"]


def _stub(monkeypatch, procs):
    by_pid = {p.pid: p for p in procs}
    parents = {p.pid: _Proc(p.info["ppid"], p._parent_name, None) for p in procs}

    class _Fake:
        @staticmethod
        def process_iter(fields=None):
            return list(procs)

        @staticmethod
        def Process(pid):
            if pid in by_pid:
                return by_pid[pid]
            for p in procs:
                if p.info["ppid"] == pid:
                    return parents[p.pid]
            raise LookupError(pid)

    monkeypatch.setattr(discover_mod, "_psutil", _Fake, raising=False)
    monkeypatch.setitem(__import__("sys").modules, "psutil", _Fake)


def test_a_claude_session_is_found(monkeypatch):
    _stub(monkeypatch, [_Proc(1, "claude.exe", REPOS + r"\alpha")])

    assert discover_mod.live_sessions() == {norm(REPOS + r"\alpha"): 1}


def test_a_codex_session_is_found(monkeypatch):
    _stub(monkeypatch, [_Proc(2, "codex.exe", REPOS + r"\beta")])

    assert discover_mod.live_sessions() == {norm(REPOS + r"\beta"): 2}


def test_both_kinds_are_reported_together(monkeypatch):
    _stub(monkeypatch, [
        _Proc(1, "claude.exe", REPOS + r"\alpha"),
        _Proc(2, "codex.exe", REPOS + r"\beta"),
    ])

    assert discover_mod.live_agents() == {
        norm(REPOS + r"\alpha"): "claude",
        norm(REPOS + r"\beta"): "codex",
    }


def test_the_desktop_app_codex_is_excluded(monkeypatch):
    """A second codex.exe runs under ChatGPT.exe. It is not a terminal tab,
    and relaunching it into one would be wrong."""
    _stub(monkeypatch, [
        _Proc(3, "codex.exe", r"C:\Program Files\WindowsApps\OpenAI.Codex\app",
              parent_name="ChatGPT.exe", ppid=99),
    ])

    assert discover_mod.live_sessions() == {}
    assert discover_mod.live_agents() == {}


def test_a_process_with_no_readable_cwd_is_skipped(monkeypatch):
    _stub(monkeypatch, [_Proc(4, "codex.exe", None)])

    assert discover_mod.live_sessions() == {}


def test_an_unrelated_process_is_ignored(monkeypatch):
    _stub(monkeypatch, [_Proc(5, "notepad.exe", REPOS + r"\gamma")])

    assert discover_mod.live_sessions() == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_discover_agents.py -q`
Expected: FAIL — Codex sessions are not found, and `live_agents` does not exist.

- [ ] **Step 3: Rewrite `live_sessions` and add `live_agents`**

Replace the body of `live_sessions` (`reloaded/discover.py:136`) with:

```python
# Codex also runs as a child of the desktop app, where it is not a terminal
# tab at all. Relaunching that into a tab would be wrong, so it is excluded by
# its parent rather than by guessing from its cwd.
DESKTOP_HOSTS = ("chatgpt.exe",)


def _sessions() -> dict[str, tuple[int, str]]:
    """Normalised cwd -> (pid, kind) for every live agent session."""
    try:
        import psutil
    except ImportError:
        return {}

    from . import agents as agents_mod

    by_process = {a.process: a.kind for a in agents_mod.AGENTS.values()}
    out: dict[str, tuple[int, str]] = {}
    for proc in psutil.process_iter(["pid", "name", "ppid"]):
        kind = by_process.get((proc.info.get("name") or "").lower())
        if kind is None:
            continue
        try:
            parent = psutil.Process(proc.info["ppid"])
            if (parent.name() or "").lower() in DESKTOP_HOSTS:
                continue
        except Exception:
            # An unreadable parent is not grounds to drop a session that is
            # otherwise a normal match.
            pass
        try:
            cwd = proc.cwd()
        except Exception:
            continue
        if cwd:
            out[norm(cwd)] = (int(proc.info["pid"]), kind)
    return out


def live_sessions() -> dict[str, int]:
    """Map normalized cwd -> pid for every running agent session.

    Ground truth for liveness, and correct for an idle session, which is why
    no heartbeat is needed. Signature unchanged from when this only knew about
    Claude Code - nine call sites depend on it.
    """
    return {cwd: pid for cwd, (pid, _kind) in _sessions().items()}


def live_agents() -> dict[str, str]:
    """Map normalized cwd -> agent kind, alongside live_sessions."""
    return {cwd: kind for cwd, (_pid, kind) in _sessions().items()}
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_discover_agents.py tests/test_discover.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_discover_agents.py reloaded/discover.py
git commit -m "feat(discover): find Codex sessions, excluding the desktop app"
```

---

### Task 5: `Tab` carries its kind and command

**Files:**
- Modify: `reloaded/layout.py:20-28`
- Test: `tests/test_layout_agent.py`

**Interfaces:**
- Consumes: `agents.DEFAULT_KIND` (Task 3)
- Produces: `Tab(cwd, title, low_confidence=False, pinned=False, agent="claude", command="")`

- [ ] **Step 1: Write the failing tests**

```python
"""A tab remembers which agent it ran and how it was launched."""
from __future__ import annotations

import json

from reloaded.layout import Layout, Tab, load, save
from conftest import make_layout, make_window


def test_a_tab_defaults_to_claude():
    assert Tab(cwd="c:/a", title="a").agent == "claude"


def test_a_tab_defaults_to_no_captured_command():
    assert Tab(cwd="c:/a", title="a").command == ""


def test_the_fields_round_trip_through_a_saved_layout(tmp_path):
    path = tmp_path / "l.json"
    lo = make_layout([make_window([0, 0, 100, 100], [
        Tab(cwd=r"C:\repos\beta", title="beta", agent="codex",
            command="codex resume --last"),
    ])])
    save(lo, path)

    back = load(path)

    tab = back.windows[0].tabs[0]
    assert tab.agent == "codex"
    assert tab.command == "codex resume --last"


def test_a_layout_written_before_kinds_existed_still_loads(tmp_path):
    """The compatibility requirement. No migration, no rewrite."""
    path = tmp_path / "old.json"
    path.write_text(json.dumps({
        "version": 1,
        "saved_ts": "t",
        "monitors": [],
        "windows": [{
            "monitor": r"\\.\DISPLAY1", "rect": [0, 0, 100, 100],
            "state": "normal", "dpi": 96, "inset": [0, 0, 0, 0],
            "tabs": [{"cwd": r"C:\repos\alpha", "title": "alpha"}],
        }],
    }), encoding="utf-8")

    tab = load(path).windows[0].tabs[0]

    assert tab.agent == "claude"
    assert tab.command == ""


def test_an_explicit_null_agent_reads_as_claude(tmp_path):
    path = tmp_path / "null.json"
    path.write_text(json.dumps({
        "version": 1, "saved_ts": "t", "monitors": [],
        "windows": [{
            "monitor": r"\\.\DISPLAY1", "rect": [0, 0, 100, 100],
            "state": "normal", "dpi": 96, "inset": [0, 0, 0, 0],
            "tabs": [{"cwd": r"C:\repos\alpha", "title": "alpha",
                      "agent": None, "command": None}],
        }],
    }), encoding="utf-8")

    tab = load(path).windows[0].tabs[0]

    assert tab.agent == "claude"
    assert tab.command == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_layout_agent.py -q`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'agent'`

- [ ] **Step 3: Extend `Tab`**

In `reloaded/layout.py`, replace the `Tab` dataclass with:

```python
@dataclass
class Tab:
    cwd: str
    title: str
    low_confidence: bool = False
    # A tab added by hand for a repo that is not currently open. The periodic
    # reconcile rebuilds the layout from live reality, so without this flag an
    # edit like "also launch `sample-repo` next time" would be erased within minutes.
    pinned: bool = False
    # Which CLI ran here. Absent in every layout written before a second kind
    # existed, so it defaults rather than being required - no migration.
    agent: str = "claude"
    # The command this session was actually launched with, read from the live
    # process at capture time. Empty means "use the kind's default". Captured
    # rather than assumed because the flags are the user's choice, and
    # reloaded's whole premise is putting things back as they were.
    command: str = ""

    def __post_init__(self):
        # A key present but null in the JSON would otherwise defeat the
        # dataclass default and put None where a string is expected.
        if not self.agent:
            self.agent = "claude"
        if not self.command:
            self.command = ""
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_layout_agent.py tests/test_layout.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_layout_agent.py reloaded/layout.py
git commit -m "feat(layout): tabs carry their agent kind and launch command"
```

---

### Task 6: capture records the kind and the command

**Files:**
- Modify: `reloaded/capture.py:12-33` (`resolve_tab`), `reloaded/capture.py:35` (`build_layout`)
- Test: `tests/test_capture_agent.py`

**Interfaces:**
- Consumes: `discover.live_agents()` (Task 4), `Tab(agent=, command=)` (Task 5)
- Produces: `session_command(pid: int) -> str` in `reloaded/discover.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Capture records what kind of session a tab held, and its command line."""
from __future__ import annotations

import reloaded.discover as discover_mod


class _Proc:
    def __init__(self, argv=None, raises=None):
        self._argv = argv or []
        self._raises = raises

    def cmdline(self):
        if self._raises:
            raise self._raises
        return list(self._argv)


def _stub(monkeypatch, proc):
    class _Fake:
        @staticmethod
        def Process(pid):
            if proc is None:
                raise LookupError(pid)
            return proc

    monkeypatch.setitem(__import__("sys").modules, "psutil", _Fake)


def test_the_running_command_is_read_back(monkeypatch):
    _stub(monkeypatch, _Proc([
        r"C:\Users\k\bin\codex.exe", "resume", "--last",
        "--dangerously-bypass-approvals-and-sandbox",
    ]))

    assert discover_mod.session_command(7) == (
        "codex resume --last --dangerously-bypass-approvals-and-sandbox"
    )


def test_the_executable_is_reduced_to_its_bare_name(monkeypatch):
    """A captured absolute path would pin the layout to one install location."""
    _stub(monkeypatch, _Proc([r"C:\Program Files\x\claude.exe", "--continue"]))

    assert discover_mod.session_command(7) == "claude --continue"


def test_an_argument_containing_a_space_is_quoted(monkeypatch):
    _stub(monkeypatch, _Proc([r"C:\x\codex.exe", "--profile", "my profile"]))

    assert discover_mod.session_command(7) == 'codex --profile "my profile"'


def test_an_unreadable_process_yields_no_command(monkeypatch):
    """Falls back to the kind's default rather than failing the capture."""
    _stub(monkeypatch, _Proc(raises=PermissionError("denied")))

    assert discover_mod.session_command(7) == ""


def test_a_vanished_process_yields_no_command(monkeypatch):
    _stub(monkeypatch, None)

    assert discover_mod.session_command(7) == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_capture_agent.py -q`
Expected: FAIL with `AttributeError: module 'reloaded.discover' has no attribute 'session_command'`

- [ ] **Step 3: Add `session_command` to `reloaded/discover.py`**

```python
def session_command(pid: int) -> str:
    """The command line a live session is running, for a captured tab.

    The executable is reduced to its bare name: an absolute path would pin the
    saved layout to one install location, and every agent binary is on PATH by
    the time readiness lets a deploy start.

    Returns "" when the process cannot be read, which the caller treats as
    "use the kind's default" rather than as a failure - a capture that dropped
    a tab because one cmdline was unreadable would be worse than one that
    relaunches it with default flags.
    """
    try:
        import psutil

        argv = list(psutil.Process(pid).cmdline())
    except Exception:
        return ""
    if not argv:
        return ""

    argv[0] = os.path.splitext(os.path.basename(argv[0]))[0]
    return " ".join(f'"{a}"' if " " in a else a for a in argv)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_capture_agent.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_capture_agent.py reloaded/discover.py
git commit -m "feat(discover): read a live session's command line for capture"
```

---

### Task 7: `build_layout` stores kind and command on each tab

**Files:**
- Modify: `reloaded/capture.py:35` (`build_layout`), `reloaded/capture.py:128` (`capture_live`)
- Test: `tests/test_capture_agent.py` (append)

**Interfaces:**
- Consumes: `discover.live_agents()`, `discover.session_command()`, `Tab(agent=, command=)`
- Produces: `build_layout(..., kinds: dict[str, str] | None = None)` — cwd to kind; absent means all Claude.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_capture_agent.py`:

```python
from reloaded.paths import norm

REPOS = r"C:\repos"
CWD_A = r"C:\repos\alpha"
CWD_B = r"C:\repos\beta"


def test_a_captured_tab_records_its_kind(monkeypatch):
    import reloaded.capture as capture_mod

    monkeypatch.setattr(capture_mod.discover, "session_command", lambda pid: "")
    tabs = capture_mod.tabs_for_window(
        [("alpha", CWD_A, 1), ("beta", CWD_B, 2)],
        kinds={norm(CWD_A): "claude", norm(CWD_B): "codex"},
    )

    assert [t.agent for t in tabs] == ["claude", "codex"]


def test_a_captured_tab_records_its_command(monkeypatch):
    import reloaded.capture as capture_mod

    monkeypatch.setattr(capture_mod.discover, "session_command",
                        lambda pid: "codex resume --last")
    tabs = capture_mod.tabs_for_window([("beta", CWD_B, 2)],
                                       kinds={norm(CWD_B): "codex"})

    assert tabs[0].command == "codex resume --last"


def test_an_unknown_cwd_captures_as_claude(monkeypatch):
    import reloaded.capture as capture_mod

    monkeypatch.setattr(capture_mod.discover, "session_command", lambda pid: "")
    tabs = capture_mod.tabs_for_window([("alpha", CWD_A, 1)], kinds={})

    assert tabs[0].agent == "claude"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_capture_agent.py -q`
Expected: FAIL with `AttributeError: module 'reloaded.capture' has no attribute 'tabs_for_window'`

- [ ] **Step 3: Add `tabs_for_window` to `reloaded/capture.py`**

```python
def tabs_for_window(resolved, kinds=None) -> list:
    """Build the Tab list for one window from its resolved sessions.

    `resolved` is an iterable of (title, cwd, pid). `kinds` maps normalized cwd
    to agent kind; a cwd absent from it captures as Claude Code, which is what
    every pre-existing layout means.

    Extracted from build_layout so the per-tab decisions - which kind, which
    command - are testable without standing up UI Automation.
    """
    from .layout import Tab

    kinds = kinds or {}
    out = []
    for title, cwd, pid in resolved:
        out.append(Tab(
            cwd=cwd,
            title=title,
            agent=kinds.get(norm(cwd), "claude"),
            command=discover.session_command(pid),
        ))
    return out
```

Then in `capture_live`, pass the kinds through:

```python
    kinds = discover.live_agents()
```

and hand `kinds` to `build_layout`, which forwards it to `tabs_for_window`.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_capture_agent.py tests/test_capture.py tests/test_capture_guards.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_capture_agent.py reloaded/capture.py
git commit -m "feat(capture): record each tab's agent kind and launch command"
```

---

### Task 8: deploy launches each tab with its own command

**Files:**
- Modify: `reloaded/deploy.py:153` (`launcher_command`)
- Test: `tests/test_deploy_agent.py`

**Interfaces:**
- Consumes: `agents.for_kind()` (Task 3), `Tab.agent`, `Tab.command` (Task 5)
- Produces: `launcher_command(cwd, delay, size_bytes, agent="claude", command="")`

- [ ] **Step 1: Write the failing tests**

```python
"""Each tab relaunches with its own agent's command."""
from __future__ import annotations

import pytest

import reloaded.deploy as deploy_mod
from reloaded.deploy import launcher_command


@pytest.fixture(autouse=True)
def _pin_shell(monkeypatch):
    monkeypatch.setattr(deploy_mod, "shell_executable", lambda: "pwsh")


CWD = r"C:\repos\beta"


def test_a_claude_tab_is_unchanged():
    assert "claude --dangerously-skip-permissions --continue" in \
        launcher_command(CWD, 0, 0)


def test_a_codex_tab_uses_the_codex_default():
    cmd = launcher_command(CWD, 0, 0, agent="codex")

    assert "codex resume --last --dangerously-bypass-approvals-and-sandbox" in cmd
    assert "claude" not in cmd


def test_a_captured_command_wins_over_the_default():
    cmd = launcher_command(CWD, 0, 0, agent="codex",
                           command="codex --profile fast")

    assert "codex --profile fast" in cmd
    assert "resume --last" not in cmd


def test_an_unknown_kind_falls_back_to_claude():
    assert "claude" in launcher_command(CWD, 0, 0, agent="gemini")


def test_the_restart_loop_wraps_a_codex_tab_too():
    """In-place restart is a property of the shell, not of the agent."""
    assert "while ($true)" in launcher_command(CWD, 0, 0, agent="codex")


def test_the_environment_hygiene_applies_to_every_kind():
    """CLAUDE_CODE_CHILD_SESSION is inherited by any child of this session,
    so it must be cleared regardless of what runs in the tab."""
    cmd = launcher_command(CWD, 0, 0, agent="codex")

    assert "$env:CLAUDE_CODE_CHILD_SESSION=$null" in cmd
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_deploy_agent.py -q`
Expected: FAIL with `TypeError: launcher_command() got an unexpected keyword argument 'agent'`

- [ ] **Step 3: Parameterise `launcher_command` and `restart_loop`**

In `reloaded/deploy.py`, change `restart_loop` to take the command, and
`launcher_command` to take the kind:

```python
def restart_loop(cwd: str, invocation: str) -> str:
    """Wrap `invocation` so `restart <repo>` can relaunch it without the tab
    ever closing. (Body otherwise unchanged - replace the CLAUDE_COMMAND
    reference in the joined body with `invocation`.)"""
```

```python
def launcher_command(cwd: str, delay: int, size_bytes: int,
                     agent: str = "claude", command: str = "") -> str:
    """The PowerShell command run inside one tab.

    `command` is what capture read off the live process; empty means fall back
    to the kind's default. The tab's own restart loop and the environment
    hygiene around it are the same for every kind - only the invocation in the
    middle differs.
    """
    from . import agents as agents_mod

    invocation = command or agents_mod.for_kind(agent).launch
    name = os.path.basename(cwd.rstrip("\\/")) or cwd
    parts = [CHILD_SESSION_CLEAR, RESUME_SUPPRESSOR]

    if size_bytes >= SIZE_WARN_BYTES:
        warn = f"[reloaded] {name} - transcript {human_size(size_bytes)}, consider /compact"
        parts.append(f"Write-Host '{_ps_quote(warn)}' -ForegroundColor Yellow")
    else:
        parts.append(f"Write-Host '{_ps_quote('[reloaded] ' + name)}' -ForegroundColor DarkGray")

    if delay > 0:
        parts.append(f"Start-Sleep {delay}")

    parts.append(CLAUDE_STARTED_AT)
    parts.append(restart_loop(cwd, invocation))
    parts.append(CLOSE_TAB_IF_STARTED)
    return "; ".join(parts)
```

Then update `wt_argv` and `wt_argv_single_tab` to pass `tab.agent` and
`tab.command` through.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_deploy_agent.py tests/test_deploy.py tests/test_restart_loop.py tests/test_relaunch_script.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_deploy_agent.py reloaded/deploy.py
git commit -m "feat(deploy): launch each tab with its own agent command"
```

---

### Task 9: teardown quits each kind its own way

**Files:**
- Modify: `reloaded/tabs.py` (`send_exit_keystrokes`), `reloaded/teardown.py:100-120`
- Test: `tests/test_teardown_agent.py`

**Interfaces:**
- Consumes: `agents.for_kind().quit_keys` (Task 3)
- Produces: `send_quit_keystrokes(keys: tuple, dismiss_overlay: bool = True)` in `tabs.py`; `plan_down(..., kinds: dict[str, str] | None = None)`

- [ ] **Step 1: Write the failing tests**

```python
"""Each kind is quit with its own keystrokes."""
from __future__ import annotations

import sys
import types

import pytest

import reloaded.tabs as tabs_mod


@pytest.fixture
def keys(monkeypatch):
    log = []
    fake = types.ModuleType("uiautomation")
    fake.SendKeys = lambda text, **kw: log.append(("keys", text))
    monkeypatch.setitem(sys.modules, "uiautomation", fake)
    monkeypatch.setattr("time.sleep", lambda s: log.append(("sleep", s)))
    return log


def _sent(log):
    return [t for kind, t in log if kind == "keys"]


def test_claude_keys_are_typed_then_submitted(keys):
    tabs_mod.send_quit_keystrokes(("/exit", "{Enter}"))

    assert _sent(keys) == ["{Esc}", "/exit", "{Enter}"]


def test_codex_keys_are_two_interrupts(keys):
    tabs_mod.send_quit_keystrokes(("{Ctrl}c", "{Ctrl}c"))

    assert _sent(keys) == ["{Esc}", "{Ctrl}c", "{Ctrl}c"]


def test_a_pause_separates_every_send(keys):
    """Claude Code's slash menu will not accept an Enter arriving right behind
    the command, and two interrupts sent together read as one."""
    tabs_mod.send_quit_keystrokes(("/exit", "{Enter}"))

    typed = [i for i, (k, t) in enumerate(keys) if t == "/exit"][0]
    entered = [i for i, (k, t) in enumerate(keys) if t == "{Enter}"][0]
    waits = [s for k, s in keys[typed:entered] if k == "sleep"]
    assert waits and sum(waits) >= 0.5


def test_the_retry_skips_the_escape(keys):
    tabs_mod.send_quit_keystrokes(("{Ctrl}c",), dismiss_overlay=False)

    assert "{Esc}" not in _sent(keys)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_teardown_agent.py -q`
Expected: FAIL with `AttributeError: module 'reloaded.tabs' has no attribute 'send_quit_keystrokes'`

- [ ] **Step 3: Generalise the keystroke sender**

In `reloaded/tabs.py`, add:

```python
def send_quit_keystrokes(keys: tuple, dismiss_overlay: bool = True) -> None:
    """Type one agent's quit sequence into whatever terminal has focus.

    Each element is sent on its own with MENU_SETTLE_SECONDS between, never
    joined. Claude Code's slash-command menu will not accept an Enter arriving
    ten milliseconds behind the command, and two interrupts sent together read
    as one.

    `dismiss_overlay` sends Escape first, to clear a transient overlay that
    would otherwise eat the first keystroke. Skipped on a retry, where Escape
    would cancel the very confirmation the retry exists to answer.
    """
    import time

    import uiautomation as auto

    if dismiss_overlay:
        auto.SendKeys("{Esc}")
        time.sleep(0.1)
    for i, key in enumerate(keys):
        if i:
            time.sleep(MENU_SETTLE_SECONDS)
        auto.SendKeys(key)
```

Keep `send_exit_keystrokes` as a thin wrapper so existing callers and tests
still pass:

```python
def send_exit_keystrokes(*, dismiss_overlay: bool = True) -> None:
    """Claude Code's quit sequence. Kept for callers that predate agent kinds."""
    send_quit_keystrokes(("/exit", "{Enter}"), dismiss_overlay=dismiss_overlay)
```

Then in `teardown.py`, thread the kind through `plan_down` into `_send_exit`,
looking the keys up with `agents.for_kind(kind).quit_keys`.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_teardown_agent.py tests/test_teardown.py tests/test_teardown_filter.py tests/test_teardown_before_exit.py tests/test_exit_keystroke_timing.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_teardown_agent.py reloaded/tabs.py reloaded/teardown.py
git commit -m "feat(teardown): quit each agent kind with its own keystrokes"
```

---

### Task 10: readiness waits only for the binaries the layout needs

**Files:**
- Modify: `reloaded/readiness.py:55-80`
- Test: `tests/test_readiness_agent.py`

**Interfaces:**
- Consumes: `agents.for_kind().binary` (Task 3), `Tab.agent` (Task 5)
- Produces: `required_binaries(lo: Layout) -> list[str]`

- [ ] **Step 1: Write the failing tests**

```python
"""A layout waits for the binaries it actually needs."""
from __future__ import annotations

import reloaded.readiness as readiness_mod
from reloaded.layout import Tab
from conftest import make_layout, make_window


def _layout(*kinds):
    tabs = [Tab(cwd=f"c:/repos/{k}{i}", title=f"{k}{i}", agent=k)
            for i, k in enumerate(kinds)]
    return make_layout([make_window([0, 0, 100, 100], tabs)])


def test_a_claude_only_layout_needs_claude():
    assert readiness_mod.required_binaries(_layout("claude")) == ["claude"]


def test_a_codex_only_layout_does_not_wait_for_claude():
    """The bug this fixes: a Codex-only layout blocked forever on a binary it
    never needed."""
    assert readiness_mod.required_binaries(_layout("codex")) == ["codex"]


def test_a_mixed_layout_needs_both():
    assert readiness_mod.required_binaries(_layout("claude", "codex")) == \
        ["claude", "codex"]


def test_an_empty_layout_needs_nothing():
    assert readiness_mod.required_binaries(make_layout([])) == []


def test_a_binary_is_listed_once_however_many_tabs_use_it():
    assert readiness_mod.required_binaries(_layout("codex", "codex")) == ["codex"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_readiness_agent.py -q`
Expected: FAIL with `AttributeError: module 'reloaded.readiness' has no attribute 'required_binaries'`

- [ ] **Step 3: Add `required_binaries` and use it in `wait_for_ready`**

```python
def required_binaries(lo) -> list[str]:
    """Which agent binaries this layout's tabs actually need on PATH.

    Waiting unconditionally for `claude` blocked a Codex-only layout forever on
    something it never used.
    """
    from . import agents as agents_mod

    return sorted({
        agents_mod.for_kind(t.agent).binary
        for w in lo.windows for t in w.tabs
    })
```

Then in `wait_for_ready`, replace the single `have_claude` flag with a set of
outstanding binaries:

```python
    needed = required_binaries(lo)
    missing = set(needed)
    while True:
        if not have_wt:
            have_wt = shutil.which("wt") is not None
        missing = {b for b in missing if shutil.which(b) is None}

        if not have_wt:
            reason = "wt.exe not on PATH"
        elif missing:
            reason = f"{', '.join(sorted(missing))} not on PATH"
        else:
            ...
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_readiness_agent.py tests/test_readiness.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_readiness_agent.py reloaded/readiness.py
git commit -m "fix(readiness): wait only for the binaries the layout needs"
```

---

### Task 11: the torn-transcript guard runs per kind

**Files:**
- Modify: `reloaded/__main__.py:84` (`cmd_capture`'s prune), `reloaded/deploy.py` (`plan_deploy` repair step)
- Test: `tests/test_transcript_agent.py`

**Interfaces:**
- Consumes: `agents.for_kind().sessions_dir` (Task 3)
- Produces: `transcript.guards_for(kind: str) -> bool` — whether this kind has a torn-tail guard at all.

- [ ] **Step 1: Write the failing tests**

```python
"""The torn-transcript repair is Claude Code's, and says so."""
from __future__ import annotations

import reloaded.transcript as transcript_mod


def test_claude_has_a_torn_tail_guard():
    assert transcript_mod.guards_for("claude") is True


def test_codex_has_no_torn_tail_guard():
    """Codex keeps its own rollout files; nothing here has been verified
    against them, and running Claude's repair over them would be a guess."""
    assert transcript_mod.guards_for("codex") is False


def test_an_unknown_kind_is_not_guarded():
    assert transcript_mod.guards_for("gemini") is False


def test_an_empty_kind_is_treated_as_claude():
    """Matching layout back-compat: absent means Claude Code."""
    assert transcript_mod.guards_for("") is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_transcript_agent.py -q`
Expected: FAIL with `AttributeError: module 'reloaded.transcript' has no attribute 'guards_for'`

- [ ] **Step 3: Add `guards_for` and gate the repair**

```python
# Which kinds have a verified torn-tail guard. Claude Code's transcript format
# and failure mode are known here; Codex's rollout files are not, and running
# this repair over them would be a guess with a destructive edit at the end.
_GUARDED_KINDS = {"claude"}


def guards_for(kind) -> bool:
    """Whether `kind` has a torn-transcript guard. Absent reads as Claude."""
    return (str(kind or "claude").strip().lower()) in _GUARDED_KINDS
```

Then in `deploy.plan_deploy`, skip the repair for tabs whose kind is not
guarded, and in `__main__.cmd_capture` leave the prune as-is (it only walks
Claude Code's projects dir, which is correct and now explicit).

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_transcript_agent.py tests/test_transcript.py tests/test_deploy.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_transcript_agent.py reloaded/transcript.py reloaded/deploy.py
git commit -m "feat(transcript): run the torn-tail guard only for kinds it fits"
```

---

### Task 12: the trust prompt is reported, never answered

**Files:**
- Modify: `reloaded/__main__.py` (`_deploy_layout` reporting)
- Test: `tests/test_trust_prompt.py`

**Interfaces:**
- Consumes: `discover.live_sessions()` (Task 4)
- Produces: `_never_started(lo, live) -> list[str]` in `__main__.py`

- [ ] **Step 1: Write the failing tests**

```python
"""A tab that launched but has no session is reported, not clicked through."""
from __future__ import annotations

import reloaded.__main__ as main_mod
from reloaded.layout import Tab
from reloaded.paths import norm
from conftest import make_layout, make_window

CWD_A = r"C:\repos\alpha"
CWD_B = r"C:\repos\beta"


def _layout(*cwds):
    tabs = [Tab(cwd=c, title=c.rsplit("\\", 1)[-1], agent="codex") for c in cwds]
    return make_layout([make_window([0, 0, 100, 100], tabs)])


def test_a_tab_with_a_live_session_is_not_reported():
    lo = _layout(CWD_A)
    assert main_mod._never_started(lo, {norm(CWD_A): 1}) == []


def test_a_tab_that_never_started_is_reported():
    """Codex asks whether to trust a directory it has not seen, and waits.
    An unattended `up` sits at that prompt instead of starting."""
    lo = _layout(CWD_A)
    assert main_mod._never_started(lo, {}) == [CWD_A]


def test_every_silent_tab_is_named():
    lo = _layout(CWD_A, CWD_B)
    assert main_mod._never_started(lo, {}) == [CWD_A, CWD_B]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_trust_prompt.py -q`
Expected: FAIL with `AttributeError: module 'reloaded.__main__' has no attribute '_never_started'`

- [ ] **Step 3: Add `_never_started` and report it after a deploy**

```python
def _never_started(lo, live) -> list[str]:
    """Tabs the deploy launched that have no live session behind them.

    Usually Codex asking whether to trust a directory it has not seen: the tab
    opens, the prompt waits, and no session ever starts. Reported rather than
    answered - it is a security question, and a launcher should not click
    through one on the user's behalf.
    """
    return [t.cwd for w in lo.windows for t in w.tabs if norm(t.cwd) not in live]
```

Call it after the post-deploy settle in `_deploy_layout` and print:

```python
    silent = _never_started(lo, discover_mod.live_sessions())
    if silent:
        print(f"[reloaded] {len(silent)} tab(s) opened but no session started:")
        for cwd in silent[:6]:
            print(f"    - {cwd}")
        print("    A Codex tab in a directory it has not seen waits for you to")
        print("    trust the contents before it starts. Answer it in the tab.")
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_trust_prompt.py tests/test_main.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_trust_prompt.py reloaded/__main__.py
git commit -m "feat(up): report tabs that opened without starting a session"
```

---

### Task 13: README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add an agent-kinds section**

```markdown
## Agent kinds

A managed tab may hold a Claude Code session or a Codex CLI one. `reloaded`
tells them apart by the running process (`claude.exe` / `codex.exe`) and
records the kind on the tab when it captures.

A second `codex.exe` runs as a child of the Codex desktop app. That is not a
terminal tab, so it is excluded by its parent process — relaunching it into a
tab would be wrong.

**Each tab remembers how it was launched.** Capture reads the live process's
own command line, so a session comes back with the flags it was running,
rather than with whatever this tool would have chosen. A tab with no captured
command falls back to its kind's default.

**Quitting differs by kind.** Claude Code takes `/exit` then Enter, with a
pause between — its slash-command menu will not accept an Enter arriving right
behind the command. Codex takes Ctrl+C twice. Both are sent one keystroke at a
time.

**Readiness waits only for what a layout needs.** A layout of Codex tabs does
not wait for `claude` on PATH.

**Codex asks about a directory it has not seen.** It prompts to trust the
contents before starting, so an unattended `up` into a fresh repo opens the
tab and waits. `up` reports tabs that opened without starting a session; it
does not answer the prompt for you, because that is a security question.
```

- [ ] **Step 2: Verify the README's own test still passes**

Run: `python -m pytest tests/test_readme.py -q`
Expected: PASS

- [ ] **Step 3: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: agent kinds, and what differs between them"
```

---

## Verification against a live machine

Not a task — these cannot be asserted in pytest, and this project has already
produced six confident wrong explanations from inference. Each is stated as
unproven until it has actually been run.

- [x] `reloaded status` lists the Codex tab alongside the Claude ones
- [x] `reloaded capture` records `agent: "codex"` and the real command line —
      confirmed in the REAL saved layout, written unattended by the 5-minute
      reconcile task rather than by a hand-run capture
- [x] `reloaded restart <codex-repo> --dry-run` names the right tab, once
      (finding the missing plan_down dedup in the process)
- [ ] `reloaded restart <codex-repo>` quits and relaunches it — needs a repo
      the user has named expendable, and `constructicon` is mid-task
- [ ] `reloaded up` into a fresh directory reports the trust prompt rather
      than hanging silently

Two additional bugs were found by running these rather than by reasoning, and
both are fixed: `plan_down` never deduped by cwd (one window here holds two
tabs titled `constructicon` behind a single process), and readiness resolved
binaries only against the PATH this process inherited, so an installer that
updated PATH afterwards made a live binary look missing.
