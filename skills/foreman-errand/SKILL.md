---
name: foreman-errand
description: This skill should be used when the user asks to "delegate to ollama", "use the local model", "summarize this locally", or "keep this off the cloud", or for token-heavy low-reasoning work such as summarizing a large log, generating boilerplate, or drafting a first-pass commit message, when the reason is a Claude usage limit, privacy, or keeping a large input out of the conversation. It does not reliably reduce Claude token cost; treat it as a way to move work off Claude, not to make it cheaper.
---

# Foreman Errand

Send a local model to fetch something instead of walking over yourself.
Offload token-heavy, low-reasoning-density subtasks to a local Ollama
model on the user's GPU, instead of spending Claude usage on them. Claude
still does the planning, the real reasoning, and the final integration.
This skill only hands off the mechanical, bulk-token part of the work.

## When to use this

Good fits (high token volume, low reasoning depth):
- Summarizing a large log file, build output, or file before reading it in full
- First-pass generation of boilerplate, docstrings, or repetitive code patterns
- Drafting a commit message from a diff
- Rough translation or reformatting of text or data

Bad fits, where doing the work directly beats delegating it:
- Anything requiring multi-file reasoning, architectural judgment, or
  understanding this specific codebase's conventions
- Small tasks where writing the prompt costs more than it saves
- Anything where correctness matters more than speed. Local models
  hallucinate more readily than Claude, especially on unfamiliar APIs and
  libraries, and an errand that comes back wrong isn't actually faster.

## How to use it

The local model has **no access to this conversation**. Every prompt must
be fully self-contained: state the goal, paste the relevant content,
specify the output format wanted.

1. Write the prompt to a scratch file rather than inlining large content
   on the command line (avoids shell quoting and length issues on
   Windows).
2. Call the helper script:

```bash
python "$HOME/.claude/skills/foreman-errand/scripts/ask_ollama.py" \
  --model gpt-oss:20b --effort low --file /path/to/prompt.txt
```

- `--effort low` (default) is fine for summarization and boilerplate,
  fast and cheap. Bump to `--effort medium` only if low-effort output is
  clearly weak for the specific task.
- Omit `--model` to use the default (`gpt-oss:20b`), a reasonable
  general-purpose pick for this kind of no-tools single-shot call. Pass a
  different model if the user prefers something else, or if
  `foreman-recommend` has identified a better fit for this machine.
- The script prints the model's reply to stdout and exits non-zero with a
  clear error if Ollama isn't running or the model isn't pulled. Check
  for that and fall back to doing the task directly rather than retrying
  blindly into the same wall.
- **For genuinely large input** (a multi-thousand-line log, a big build
  output): grep it down to the relevant lines *before* delegating, don't
  hand over the whole thing. The script refuses input over roughly 28K
  estimated tokens rather than silently overflowing the context window,
  because that failure mode is worse than an error: a prompt too big for
  the context doesn't crash, it just produces a confused, generic
  non-answer with nothing to signal that anything went wrong. A ~340KB log
  summarized directly failed exactly this way and gave no indication of
  it. Grepping it down to the ERROR and WARN lines first, 488 of roughly
  4,000 lines, worked cleanly and correctly surfaced the two real errors
  buried in the noise.

3. Treat the output as a first draft. Skim it before using it verbatim.
   Local models are meaningfully weaker than Claude, so spot-check
   anything that will be committed or shown to the user, and redo it
   directly if it's wrong rather than trying to polish a bad local
   generation into a good one.

## Notes

- This is a single stateless call, not a conversation. There's no session
  or history to manage. For a task that needs multiple turns of local
  back-and-forth or unattended tool use (running commands, editing
  files), use OpenCode instead (see `docs/opencode-setup.md` in the
  foreman repo, or the `foreman-build` skill).
- If Ollama isn't installed or isn't running, say so plainly and fall
  back to doing the task directly, rather than quietly burning more
  effort trying to fix the local setup mid-task.
