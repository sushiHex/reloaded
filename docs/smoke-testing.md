# Smoke testing

The test suite never touches a real desktop. A release is checked live with
`tools/smoke.py`, against throwaway repos under `~/reloaded-smoke` (#9 holds
the checklist and its results).

The kit only acts on sessions under that root. It types only into a tab it has
just proved selected and in front, and only when that tab's title is unique in
its window. Run it from PowerShell. Git Bash rewrites `/exit` into
`C:/Program Files/Git/exit` before Python sees it; `keys` refuses that, but
any other command would pass it on to the agent as a prompt.

## A run

```powershell
python tools/smoke.py setup alpha:claude beta:codex a/twin:claude b/twin:codex
python -m reloaded --layout smoke up
python tools/smoke.py wait 4
python tools/smoke.py screen alpha              # the tab's last lines
python -m reloaded --repos-root $HOME\reloaded-smoke restart alpha
python tools/smoke.py keys alpha /exit {Enter}  # one press at a time
python tools/smoke.py end                       # stop every smoke agent
python tools/smoke.py teardown
```

`setup` backs up `~/.reloaded/layouts/default.json` first, and refuses to run
over an earlier backup. The periodic capture saves the smoke tabs into the
default layout while they run. `teardown` checks that only smoke tabs changed,
then puts the backup back byte for byte. It also deletes the fixtures, the
`smoke` layout and their Claude transcript folders. The trust entries the
agents recorded for the fixtures (`~/.claude.json`, Codex's `config.toml`)
are the agents' own files, so `teardown` names them and leaves them alone.

## What a run has to know

- **A fresh fixture is untrusted.** Claude Code shows its folder-trust dialog
  before it looks for a conversation, so `--continue`'s failure comes after the
  dialog is answered, not at once. To answer: `keys <repo> {Down} {Enter}` for
  Claude, `keys <repo> {Enter}` for Codex. Esc means "No, exit" to Claude.
- **Codex records a session only after a prompt.** Checking Codex resume costs
  one model reply (`keys beta "Reply with only the word ok" {Enter}`). Resume
  shows up as an event appended to the same rollout under
  `~/.codex/sessions/<date>/`, with no new rollout file.
- **An agent's MCP servers can outlive it by a few seconds.** `teardown` waits
  for them before deleting the fixtures.
- **`down` and the full `restart` act on every session they recognize**, not
  only smoke ones. Run those only on a desktop or account where nothing else is
  running.
- **Sign-in is checked by signing in.** Afterwards, read `~/.reloaded/reloaded.log`
  and `reloaded status`: only missing sessions should have launched, and
  nothing should have been refused.
