---
name: foreman-build
description: This skill should be used when the user wants a multi-step coding or research task done by a local model instead of Claude directly, such as "do this with the local model", "run this on OpenCode", "let the local model handle this", or any task that needs file edits, running commands, or several tool-calling turns and should save Claude usage rather than spend it. Not for single quick calls (use foreman-errand) or for figuring out which model to use (use foreman-recommend first if that isn't already known).
---

# Foreman Build

Run a real multi-step task through [OpenCode](https://opencode.ai) against a
local model, with Claude launching and reviewing rather than driving every
turn itself. This is the actual point of the whole project: Claude's usage
goes toward judgment (writing the task, verifying the result), not toward
the mechanical work in between.

Full setup, safety settings, and model notes live in this project's
`docs/opencode-setup.md` (in the `foreman` repo this skill ships with).
Read that before the first run on a new machine, since it covers a real
crash this setup can cause if two specific Ollama settings aren't in place.
This file is the short version for driving it once that's done.

## Before launching

- **Write a fully self-contained task description.** The local model sees
  nothing from this conversation except the task string. State the goal,
  constraints, relevant file paths, and what "done" looks like.
- **Know which model to use.** If that's not already established for this
  machine, run `foreman-recommend` first rather than guessing.
- **Match the checkpoint window to the model.** A fast, VRAM-light model
  (the usual default) can be trusted with a longer task before checking in.
  A model that needs CPU offload or has shown inconsistency should get a
  shorter leash and closer review.

## Launching

```bash
timeout 240 opencode run "self-contained task description" \
  -m ollama/<model> --dir /path/to/project --auto
```

Run this via the Bash tool in the background if the task is expected to
take a while. `--auto` is required for unattended use, but it also bypasses
OpenCode's own repeat-call safety check (`doom_loop` defaults to `ask`,
which `--auto` auto-approves). The external `timeout` is what actually
bounds a run that goes wrong, not anything internal to OpenCode. Pick the
timeout to match the task's real complexity; a trivial edit needs under a
minute, something touching several files can need several.

## Reviewing the result: do not skip this

**Never trust the model's own completion message.** This has failed in
concrete, documented ways: a model that abandoned a file edit after three
failed attempts, then wrote a test file for a class that doesn't exist,
with a syntax error, and reported no problem. Another resumed a session
past completion and silently duplicated content across five files while
reporting success. Neither of those surfaced as an error in the tool's own
output. Only checking the actual result caught them.

Every time, regardless of how confident the output sounds:

1. Read the actual file(s) changed, or run `git diff` if the working
   directory is a repo.
2. Run any tests or verification the task called for, and read the actual
   output rather than trusting a summary of it.
3. Only then report the task done.

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
