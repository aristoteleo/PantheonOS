# Parser fixtures

Each file is one agent's stdout for one headless turn, a JSON object per
line. Paths are shortened to `/work` (`/work/acme` is the workspace, a small
git repository with `README.md`, `src/calc.py` and a `night-sky` skill in
`.agents/skills`); nothing else is edited.

| File | Agent | Run |
|---|---|---|
| `claude.jsonl`, `codex.jsonl`, `cursor.jsonl` | Claude Code, Codex, Cursor Agent | real: "cat a.txt, then reply with one word" |
| `gemini.jsonl` | Gemini CLI | written from its documented format, not a run |
| `claude-subagents-*.jsonl` | Claude Code 2.1 | real: sub-agents defined with `--agents` (see `subagents_test.go`) |
| `codex-canvas.jsonl` | Codex 0.165 | real: read two files, add a function, run it, search the web; `-c model_reasoning_effort="low"` |
| `codex-research-off.jsonl` | Codex 0.165 | real: the same session resumed with `--add-dir` (before `resume`), `-i` an image, `-c web_search="disabled"`, asked to search the web |
| `codex-skill.jsonl` | Codex 0.165 | real: a question the `night-sky` skill answers |
| `codex-subagents.jsonl` | Codex 0.165 | real: asked to use two sub-agents in parallel (below) |
| `cursor-canvas.jsonl` | Cursor Agent 2026.10.22 | real: the same task as `codex-canvas` |
| `cursor-resume.jsonl` | Cursor Agent 2026.10.22 | real: that session resumed with `--add-dir`, a file outside the workspace, an image path and the skill |
| `gemini-auth-error.jsonl` | Gemini CLI 0.66 | real: a turn refused by the API (no valid sign-in) |
| `pantheon.jsonl` | Pantheon Agent (`pantheon agent chat --json`, staging) | real: the same task as `codex-canvas`, on a node with `--node --workdir`; the node id renamed |
| `pantheon-resume.jsonl` | Pantheon Agent | real: that chat continued with `--chat` and `--model low`, asked what it added |
| `pantheon-subagent.jsonl` | Pantheon Agent | real: asked to run one sub-agent with `pantheon agent run` |

## Gemini CLI

No Gemini CLI tool stream could be recorded: on the machine the fixtures
come from, its Google sign-in is refused ("This client is no longer
supported for Gemini Code Assist for individuals") and the API key there is
not valid. Its parser maps tools by the names and parameters in the CLI's
own source (`read_file`, `replace`, `write_file`, `run_shell_command`,
`google_web_search`, `web_fetch`, `activate_skill`), tested with lines
written in `kinds_test.go`. For the same reason its canvas capabilities
leave out what only a run could confirm: `--policy` denying the web tools
headless, and `@path` images.

## Codex sub-agents (`codex-subagents.jsonl`)

Codex 0.165 with `multi_agent` on (its default), asked to "spawn two
sub-agents in parallel" to read one file each. The sub-agents ran (the
answer has both results), but `codex exec --json` shows only:

- two `collab_tool_call` items with `"tool":"wait"`,
  `"receiver_thread_ids":[]`, `"prompt":null` and `"agents_states":{}`,
  started and completed;
- no item for the spawns, nothing from inside the sub-agents, and nothing
  that says which sub-agent a wait was for or when one ended.

The session's rollout file (`~/.codex/sessions/…`) has what the stream
leaves out: `spawn_agent` function calls (with an encrypted message), a
`SubAgentActivity` item per sub-agent started and completed, and each
sub-agent's final message; the sub-agents' own steps are in rollout files of
their own threads. The stream alone cannot place a sub-agent's launch,
progress or end, so the parser shows a wait as an `other` step, and Codex's
canvas capabilities are `subagents: false` and `roles: false`. Roles were
not tried: Codex's configuration has per-role entries (`agents.<name>` with
`description` and `config_file`), but sub-agents run under them would not
show either.
