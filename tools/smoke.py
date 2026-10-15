"""The live smoke test's fixtures and probes (#9), so a run is a few commands.

This drives the real desktop. It is never run by the test suite: pytest
collects `tests/` only. Run it from PowerShell, not Git Bash, which rewrites
an argument like `/exit` into `C:/Program Files/Git/exit` before Python sees
it; `keys` refuses one that arrives that way.

    python tools/smoke.py setup alpha:claude beta:codex a/twin:claude b/twin:codex
    python tools/smoke.py setup --fake alpha:claude beta:codex   # or: no real agent
    python -m reloaded --layout smoke up
    python tools/smoke.py wait 4
    python tools/smoke.py screen alpha
    python tools/smoke.py keys alpha /exit {Enter}
    python tools/smoke.py end
    python tools/smoke.py teardown

`setup` backs up the default layout first. Periodic capture saves the smoke
tabs into it while they run, so `teardown` puts the backup back, after
checking that nothing but smoke tabs changed. It also deletes the fixtures
and their Claude transcript folders, and lists the trust entries the agents
recorded for them - those files are the agents' own and are left alone.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from reloaded import agents, discover, tabs, win32  # noqa: E402
from reloaded.paths import norm, tab_title  # noqa: E402

HOME = pathlib.Path.home()
ROOT = HOME / "reloaded-smoke"
LAYOUTS = HOME / ".reloaded" / "layouts"
BACKUP = ROOT / ".backup" / "default.json"
FAKES = ROOT / ".fakes"
LAYOUT = LAYOUTS / "smoke.json"


def _is_smoke(path: str) -> bool:
    return norm(path).startswith(norm(str(ROOT)) + os.sep)


def _digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _build_fakes() -> pathlib.Path:
    """tools/fake_agent.cs compiled as claude.exe and codex.exe, for a run
    that must not start a real agent."""
    csc = pathlib.Path(os.environ.get("WINDIR", r"C:\Windows"),
                       r"Microsoft.NET\Framework64\v4.0.30319\csc.exe")
    if " " in str(FAKES):
        # The launcher splices the command into PowerShell unquoted.
        sys.exit(f"{FAKES} has a space in it; the fakes cannot be launched from there")
    FAKES.mkdir(parents=True, exist_ok=True)
    source = pathlib.Path(__file__).with_name("fake_agent.cs")
    for agent in agents.AGENTS.values():
        subprocess.run([str(csc), "/nologo", f"/out:{FAKES / agent.process}", str(source)],
                       check=True)
    return FAKES


def setup(*specs: str) -> None:
    """Repos under ~/reloaded-smoke, one per `name:kind`, none with a
    conversation, and a one-window layout `smoke` holding them in order.
    With `--fake` first, each tab runs tools/fake_agent.cs in place of its
    agent. Refused while a backup exists: by then the default layout may hold
    smoke tabs, and backing that up would lose the clean copy."""
    fake = bool(specs) and specs[0] == "--fake"
    specs = specs[fake:]
    if BACKUP.exists():
        sys.exit(f"{BACKUP} exists: `teardown` the last run first")
    fakes = _build_fakes() if fake else None
    BACKUP.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(LAYOUTS / "default.json", BACKUP)
    print("backed up default.json", _digest(BACKUP))
    tabs_ = []
    for spec in specs:
        name, _, kind = spec.partition(":")
        agent = agents.for_kind(kind)
        repo = ROOT / pathlib.Path(name)
        repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        # The kind's own launch, with the fake's path for its binary: deploy
        # keeps a command's program as written.
        command = (" ".join([str(fakes / agent.process)] + agent.launch.split()[1:])
                   if fakes else "")
        tabs_.append({"cwd": str(repo), "title": tab_title(str(repo)),
                      "low_confidence": False, "pinned": True,
                      "agent": agent.kind, "command": command})
        print(f"  {tabs_[-1]['agent']:6} {repo}")
    monitor = win32.list_monitors()[0]
    LAYOUT.write_text(json.dumps({
        "version": 1, "saved_ts": "2026-01-01T00:00:00Z",
        "monitors": [{"device": monitor.device, "primary": monitor.primary,
                      "work": list(monitor.work), "dpi": monitor.dpi}],
        "windows": [{"monitor": monitor.device, "rect": [200, 200, 1400, 1000],
                     "state": "normal", "dpi": monitor.dpi, "tabs": tabs_}],
    }, indent=2), encoding="utf-8")


def _sessions() -> list:
    return sorted((s for s in discover.sessions() if _is_smoke(s.cwd)),
                  key=lambda s: s.cwd)


def wait(want: str = "1", timeout: str = "60") -> None:
    """Poll until `want` smoke sessions are live, then list them."""
    deadline = time.time() + float(timeout)
    while len(found := _sessions()) < int(want) and time.time() < deadline:
        time.sleep(2)
    for s in found:
        print(f"{s.kind:6} {s.cwd}  pid={s.pid}  window={hex(s.hwnd or 0)}")
    print(f"{len(found)} of {want} live")


def _tab(repo: str):
    """The window and tab item holding smoke repo `repo` (a path under the
    root, or a name). Refused where its title, kind mark and all, is not
    unique in its window."""
    cwd = norm(str(ROOT / pathlib.Path(repo)))
    session = next((s for s in _sessions() if norm(s.cwd) == cwd), None)
    if session is None or not session.hwnd:
        sys.exit(f"no live smoke session in a tab at {cwd}")
    title = agents.tab_label(session.kind, session.cwd)
    items = [i for t, i in tabs.list_tab_items(session.hwnd) if t == title]
    if len(items) != 1:
        sys.exit(f"{len(items)} tabs titled {title!r} in that window; cannot aim")
    return session.hwnd, items[0]


def screen(repo: str, lines: str = "25") -> None:
    """Print the last lines of the tab's terminal text. Selects the tab."""
    import uiautomation as auto

    hwnd, item = _tab(repo)
    tabs.select_tab(hwnd, item)
    time.sleep(0.5)
    term = auto.ControlFromHandle(hwnd).Control(searchDepth=25, ClassName="TermControl")
    text = term.GetPattern(auto.PatternId.TextPattern).DocumentRange.GetText(-1)
    print("\n".join([ln.rstrip() for ln in text.splitlines() if ln.strip()][-int(lines):]))


def keys(repo: str, *presses: str) -> None:
    """Send each press on its own, once the tab is proved selected and its
    window in front - as teardown does, and for teardown's reasons."""
    import uiautomation as auto

    if any(":/" in p and "Git" in p for p in presses):
        sys.exit(f"{presses}: Git Bash rewrote an argument into a path; run from PowerShell")
    hwnd, item = _tab(repo)
    win32.set_foreground(hwnd)
    if not (tabs.select_tab(hwnd, item) and tabs.tab_is_selected(item)
            and win32.is_foreground(hwnd)):
        sys.exit("could not bring the tab to the front; nothing sent")
    for press in presses:
        time.sleep(tabs.MENU_SETTLE_SECONDS)
        auto.SendKeys(press, interval=0.02)
    print("sent", list(presses))


def end() -> None:
    """Stop every smoke agent with its process tree. Its tab's own loop then
    closes the tab, and a window left with no tabs closes with it."""
    import psutil

    for s in _sessions():
        root = psutil.Process(s.pid)
        for p in root.children(recursive=True) + [root]:
            try:
                p.terminate()
            except psutil.NoSuchProcess:
                pass
        print(f"stopped {s.kind} {s.cwd} (pid {s.pid})")


def _real_tabs(path: pathlib.Path) -> list:
    """Each window's tabs, in order, without the smoke ones - and without
    the window order, which the smoke window can shift."""
    lo = json.loads(path.read_text(encoding="utf-8"))
    windows = [tuple((norm(t["cwd"]), t.get("agent"), t.get("command"))
                     for t in w["tabs"] if not _is_smoke(t["cwd"]))
               for w in lo["windows"]]
    return sorted(w for w in windows if w)


def teardown() -> None:
    """Put the default layout back, if only smoke tabs changed in it; delete
    the fixtures, the smoke layout and their transcript folders; list what
    the agents themselves kept about them."""
    if _sessions():
        sys.exit("smoke sessions are still running; `end` them first")
    live = LAYOUTS / "default.json"
    if _real_tabs(live) != _real_tabs(BACKUP):
        sys.exit("default.json changed outside the smoke tabs; left as it is, "
                 f"with the backup at {BACKUP}")
    shutil.copy2(BACKUP, live)
    print("default.json restored", _digest(live))
    LAYOUT.unlink(missing_ok=True)
    # An agent's MCP servers can outlive it by a few seconds, holding a
    # fixture as their working directory.
    for _ in range(10):
        shutil.rmtree(ROOT, ignore_errors=True)
        if not ROOT.exists():
            break
        time.sleep(2)
    else:
        sys.exit(f"{ROOT} is still held open; delete it once its processes exit")
    # Named by Claude's own rule, of which the root's folder is the prefix.
    prefix = pathlib.Path(agents.conversations_dir("claude", str(ROOT))).name.lower() + "-"
    for folder in pathlib.Path(agents.CLAUDE.sessions_dir).iterdir():
        if folder.name.lower().startswith(prefix):
            shutil.rmtree(folder)
            print("removed", folder.name)
    for kept in (HOME / ".claude.json", pathlib.Path(agents._codex_home()) / "config.toml"):
        if kept.exists() and "reloaded-smoke" in kept.read_text(encoding="utf-8").lower():
            print(f"left in {kept}: trust entries for the deleted fixtures")


if __name__ == "__main__":
    commands = {f.__name__: f for f in (setup, wait, screen, keys, end, teardown)}
    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        sys.exit(f"usage: smoke.py {{{','.join(commands)}}} [args]")
    commands[sys.argv[1]](*sys.argv[2:])
