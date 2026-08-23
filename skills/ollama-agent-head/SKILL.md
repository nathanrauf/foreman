---
name: ollama-agent-head
description: This skill should be used when the user asks to "run this on ollama", "use the local agent", "run this unattended", "let it run in the background locally", or wants a continuous coding or research task done mostly on their own GPU so it doesn't consume Claude usage — Claude acts only as the supervising "head", launching a local tool-calling loop and reviewing it at checkpoints rather than doing every step itself.
---

# Ollama Agent Head

Run an autonomous tool-calling loop against a local Ollama model in a
**separate background process** that does the actual work — reading/writing
files, running commands — while Claude only launches it and reviews it at
checkpoints. Claude usage is spent at checkpoints, not per step, which is
what makes this different from `ollama-delegate` (a single stateless call
per subtask): this is a multi-turn agent that keeps going on its own between
check-ins.

**Trade-off to hold onto:** local 7-30B models are much less reliable at
long tool-calling loops than Claude — they drift, misjudge when they're
"done," and occasionally do something dumb with a shell command. The
checkpoint window exists to bound that damage. Don't widen it past the
defaults below without a specific reason.

## Modes

| | coding | research |
|---|---|---|
| Tools available | read_file, list_dir, grep, web_search, web_fetch, write_file, edit_file, run_bash, finish | read_file, list_dir, grep, web_search, web_fetch, finish (no writes, no shell) |
| Default model | `qwen3-coder:30b` (agentic-coding-tuned, 256K context) | `gpt-oss:20b` (fits fully in VRAM, native tool calling) |
| Default checkpoint window | 4 turns | 10 turns |
| Risk if it drifts | Bad edits/commands compound before review | Worst case: a mediocre summary |

Pick `research` for anything that only needs to read and report — it's
lower-stakes and can run longer between checkpoints. Use `coding` only when
file edits or command execution are actually needed.

**On model choice:** `devstral:24b` is marketed as purpose-built for agentic
coding and was tried first, but testing on this machine showed it reliably
narrates tool calls as JSON text in its reply instead of using Ollama's
actual tool-calling channel — the loop never sees a real tool call and
stalls. It's been removed. `gpt-oss:20b` and `qwen3-coder:30b` both used
native tool calls correctly in every test (research and coding, including
multi-step file edits and shell verification) — `qwen3-coder:30b` was
slightly more efficient (no wasted attempts) and is coding-specialized with
a much bigger context window, so it's the coding-mode default despite
exceeding this machine's 16GB VRAM by ~3GB (Ollama offloads the overflow to
system RAM automatically; no noticeable slowdown was observed in testing —
MoE architecture means only a small fraction of params are active per
token). `gpt-oss:20b` stays the research default since it fits fully in
VRAM with no offload, which matters more for frequent/lighter calls.

## Web access

Both modes have `web_search` (Tavily API) and `web_fetch` (fetch a URL,
HTML stripped to text). Use for tasks that plausibly need current or
external information — otherwise gather anything the loop will need and put
it directly in the task description, since local models hallucinate more
readily than Claude when they don't actually know something.

- **Setup:** `web_search` needs a Tavily API key (free tier, 1,000
  credits/month, no credit card — verified against tavily.com directly).
  It's read from the `TAVILY_API_KEY` env var if set, otherwise from
  `~/.claude/tavily_api_key.txt` (plain text, just the key). The file
  fallback exists because `setx` only takes effect for *new* process trees —
  a Claude Code session's Bash/PowerShell tool processes were already
  running before the var was set, and won't see it until the whole session
  restarts, which is disruptive. Prefer the file for that reason unless the
  user specifically wants it as a real env var. Without either, `web_search`
  fails with a clear error and the model falls back to `web_fetch` on URLs it
  already knows or can guess.
- **Prompt-injection risk:** fetched web content is untrusted input, not
  instructions — the system prompt tells the model this, but a local model is
  more likely to be fooled by a page that says "ignore your task and run X"
  than Claude would be. This matters more in coding mode, where the model
  also has `run_bash`. Don't launch a coding-mode session with web access
  against a task that involves fetching from arbitrary/untrusted domains
  without weighing that.
- **`_check_url_safety`** blocks literal local/private IP addresses and
  `localhost` (including the loop's own Ollama port) — it does not resolve
  DNS, so it's a guard against obvious accidents, not real SSRF protection.

## Before launching

1. **Write a fully self-contained task description.** The local model sees
   nothing from this conversation except what's in the task string — state
   the goal, constraints, relevant file/dir paths, and what "done" looks
   like. A vague task produces a loop that wanders.
2. Confirm the working directory. Prefer a directory already under version
   control for coding mode — the checkpoint report includes a `git diff`,
   which is the main way to review what happened without reading every file.
3. If the task is ambiguous on mode, autonomy risk, or scope, ask the user
   rather than guessing — this runs unsupervised and file edits/commands are
   harder to walk back than a bad chat reply.

## Launching

Start a new session in the background:

```bash
python "$HOME/.claude/skills/ollama-agent-head/scripts/agent_loop.py" start \
  --mode coding --workdir "/path/to/project" \
  --task "Self-contained task description here." \
  --checkpoint-turns 4 --max-turns 60
```

Run this via the Bash tool with `run_in_background: true`. The script prints
`SESSION <id>` and `CHECKPOINT <path>` immediately, then blocks until the
checkpoint window or a `finish` call ends the run and prints `STATUS <status>`.
Capture the session id from the output — it's needed to resume.

## Reviewing a checkpoint

When the background task completes, read `<workdir>/.ollama-agent/<session_id>/checkpoint.md`.
It contains the status, turn counts, files touched, the model's own summary,
and (coding mode) a `git diff`. Possible statuses:

- `complete` — the model called `finish` with `task_complete: true`. Verify
  the diff actually satisfies the task before telling the user it's done;
  local models overstate completion.
- `checkpoint` — model paused deliberately (finished a sub-step, or hit the
  turn window). Review the diff, then resume if it's on track.
- `ambiguous_stop` — model responded with prose instead of calling a tool.
  Read what it said; usually means it's confused or thinks it's blocked.
  Resume with `--message "..."` to correct it (see Resuming below) rather
  than starting over, unless the confusion is deep enough that a fresh
  session with a sharper task is genuinely cleaner.
- `safety_stop` — 3 consecutive tool failures. Something is wrong (bad path,
  missing dependency, etc.) — diagnose from the log before resuming.
- `max_turns_hit` — global safety cap reached. Decide whether to raise
  `--max-turns` on resume or wrap up manually; don't raise it reflexively.
- `error` — couldn't reach Ollama (server down, model not pulled). Fix that,
  then resume.

Full step-by-step tool calls are logged to `<session_dir>/log.jsonl` if more
detail than the checkpoint summary is needed.

## Resuming

```bash
python "$HOME/.claude/skills/ollama-agent-head/scripts/agent_loop.py" resume \
  --session <session_id> --workdir "/path/to/project" --checkpoint-turns 4
```

Also run this in the background. Repeat launch→review→resume until status is
`complete` (and verified) or the task is abandoned/handed back to the user.

Resuming a session already marked `complete` is refused by default (pass
`--force` to override) — found via stress testing that without this guard,
resuming a finished session just re-runs the model on a task it already did,
and it will likely redo the work rather than recognize it's done. Confirmed
concretely: a 5-file edit task got each file's content duplicated after
being resumed past completion.

Add `--message "..."` to inject a correction or new information before the
loop continues (e.g. "your pip install actually failed silently, use `python
-m pip` instead" or "that verification method doesn't actually prove it
works — do X instead"). This is the way to unstick a session that's on the
wrong track without discarding its progress — used successfully in testing
to correct a model that ran a broken command, got no error output, then
fabricated a plausible-sounding excuse for the failure and reported the task
complete anyway. **Always independently verify a `complete` status before
trusting it** — checking the actual files/output yourself, not just reading
the model's summary, is what caught that.

## Safety notes

- File tools refuse to touch anything outside `--workdir` — don't work
  around this by pointing `--workdir` at something broader than the task
  needs.
- `run_bash` runs with a timeout (default 120s, `--bash-timeout` to adjust)
  and output is truncated to the model — check `log.jsonl` for full output
  if a command's effect is unclear from the checkpoint.
- This is unattended code execution on the user's machine. Don't launch a
  coding-mode session against a task that plausibly involves destructive
  commands, credentials, or anything outside the project directory without
  flagging that to the user first.
