---
name: foreman-build
description: This skill should be used when the user explicitly wants a multi-step coding task run on a local model rather than by Claude, such as "do this with the local model", "run this on OpenCode", "keep this off the cloud", or when Claude usage limits, privacy, or a long unattended run are the reason for moving work off Claude. Do NOT reach for this to save credits: measured on this project it costs slightly MORE Claude tokens than doing the task directly, because Claude token cost is dominated by context and orchestration rather than generation. Not for single quick calls (use foreman-errand) or for choosing a model (use foreman-recommend).
---

# Foreman Build

Run a real multi-step task through [OpenCode](https://opencode.ai) against a
local model, with Claude writing the task and verifying the result rather
than driving every turn itself.

**Know what this does and doesn't buy before using it.** Measured on this
project, delegating costs slightly *more* Claude tokens than doing the task
directly, at every task size tested: 31,190 against 30,050 on a small fix,
35,982 against 34,461 on a three-file module. Claude's token cost is
dominated by context, system prompt, tool definitions and reading, not by
generation, so moving the generation elsewhere saves little and the
orchestration adds overhead. It is also roughly three times slower in wall
clock.

What it does buy: work that doesn't consume Claude usage limits, code that
never leaves the machine, and long unattended runs where a local model
grinds for twenty minutes and Claude reads one paragraph at the end. Reach
for it when one of those is the actual goal. If the user just wants to
spend less, tell them plainly that switching Claude Code to a cheaper model
will do more, and don't delegate.

**Run the loop on the cheapest model that can do it.** Writing a task file,
launching, and checking a diff is mechanical. The measurements above were
produced by Haiku doing exactly that, and it lost nothing in quality
against a more expensive model. If this project's `agents/foreman-runner.md`
is installed, hand the execution to that subagent: it is pinned to Haiku,
its intermediate work stays out of the caller's context, and only its
verdict comes back.

Full setup, safety settings, and model notes live in this project's
`docs/opencode-setup.md` (in the `foreman` repo this skill ships with).
Read that before the first run on a new machine, since it covers a real
crash this setup can cause if two specific Ollama settings aren't in place.
This file is the short version for driving it once that's done.

## Before launching

- **Write a fully self-contained task description, to a file.** The local
  model sees nothing from this conversation except what's in that file.
  State the goal, constraints, relevant file paths, and what "done" looks
  like. Write it to a file and attach it (`-f`) rather than inlining it in
  the message string; see the Windows note below for why this matters, not
  just for shell-quoting convenience.
- **Know which model to use.** If that's not already established for this
  machine, run `foreman-recommend` first rather than guessing.
- **Match the checkpoint window to the model.** A fast, VRAM-light model
  (the usual default) can be trusted with a longer task before checking in.
  A model that needs CPU offload or has shown inconsistency should get a
  shorter leash and closer review.

## Launching

```bash
timeout 240 opencode run "Follow the attached task description." \
  -f task.txt -m ollama/<model> --dir /path/to/project --auto
```

Run this via the Bash tool in the background if the task is expected to
take a while. `--auto` is required for unattended use, but it also bypasses
OpenCode's own repeat-call safety check (`doom_loop` defaults to `ask`,
which `--auto` auto-approves). The external `timeout` is what actually
bounds a run that goes wrong, not anything internal to OpenCode. Pick the
timeout to match the task's real complexity; a trivial edit needs under a
minute, something touching several files can need several.

**On Windows, never put the real task inline in the message string.**
`opencode` resolves to a `.cmd` wrapper there, and cmd.exe's batch-argument
handling silently drops or reorders flags when an argument contains a
newline, which any real multi-line task description will. The practical
effect: `-m` gets ignored with no error, the run silently falls back to
whatever model `opencode.json` defaults to, and the exit code is still 0.
Confirmed concretely: a run against a real task fell back this way to a
model this project had already rejected for tool-calling, and produced
unrelated, wrong output narrated as text instead of real tool calls,
with nothing in the run's own output indicating anything had gone wrong.
The `-f task.txt` pattern above avoids this because the message string
itself stays short and single-line.

## Reviewing the result: do not skip this

**Never trust the model's own completion message.** This has failed in
concrete, documented ways: a model that abandoned a file edit after three
failed attempts, then wrote a test file for a class that doesn't exist,
with a syntax error, and reported no problem. Another resumed a session
past completion and silently duplicated content across five files while
reporting success. Neither of those surfaced as an error in the tool's own
output. Only checking the actual result caught them.

Failure isn't always total, either. On one run, the code and test suite
were both genuinely correct (independently verified, tests actually
passing), but a secondary requirement in the same task, a README
documenting each function, came back just asserting that documentation
existed without providing it, and the model reported the task complete
anyway. Check every requirement the task named, not just the main
deliverable.

Every time, regardless of how confident the output sounds:

1. Read the actual file(s) changed, or run `git diff` if the working
   directory is a repo.
2. Run any tests or verification the task called for, and read the actual
   output rather than trusting a summary of it.
3. Check every requirement the task listed individually was actually met,
   not just the central one.
4. Only then report the task done.

If something's wrong, don't just retry blindly. Either relaunch with a
sharper, corrected task description, or fix it directly if that's faster
than another round-trip through the local model.

## Model notes

See `docs/opencode-setup.md` for current recommendations and their actual
measured behavior. Model rankings here go stale fast, and a model that's
reliable on a trivial task isn't automatically reliable on a harder one.
That gap has already shown up once: a small, fast model validated on
simple read-and-edit tasks failed outright on a moderately harder one that
a larger model in the same family handled correctly. Don't extrapolate a
model's reliability past the complexity it's actually been tested at.
