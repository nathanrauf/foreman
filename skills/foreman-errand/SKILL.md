---
name: foreman-errand
description: This skill should be used when the user asks to "delegate to ollama", "use the local model", "offload this to save credits", "summarize this locally", or before performing token-heavy, low-reasoning-density work such as summarizing large files or logs, generating boilerplate/docstrings, or drafting a first-pass commit message — situations where spending Claude usage is wasteful because a local model on the user's own GPU can do it well enough.
---

# Foreman Errand

Offload token-heavy, low-reasoning-density subtasks to a local Ollama model
running on the user's GPU, instead of spending Claude usage on them. Claude
still does the planning, the real reasoning, and the final integration — this
skill only hands off the mechanical, bulk-token part of the work.

## When to use this

Good fits (high token volume, low reasoning depth):
- Summarizing a large log file, build output, or file before reading it in full
- First-pass generation of boilerplate, docstrings, or repetitive code patterns
- Drafting a commit message from a diff
- Rough translation/reformatting of text or data

Bad fits — do the work directly instead of delegating:
- Anything requiring multi-file reasoning, architectural judgment, or understanding
  this specific codebase's conventions
- Small tasks where the prompt-writing overhead exceeds the savings
- Anything where correctness matters more than speed (local models hallucinate
  more readily than Claude, especially on unfamiliar APIs/libraries)

## How to use it

The local model has **no access to this conversation** — every prompt must be
fully self-contained (state the goal, paste the relevant content, specify the
output format wanted).

1. Write the prompt to a scratch file rather than inlining large content on
   the command line (avoids shell quoting/length issues on Windows).
2. Call the helper script:

```bash
python "$HOME/.claude/skills/foreman-errand/scripts/ask_ollama.py" \
  --model gpt-oss:20b --effort low --file /path/to/prompt.txt
```

- `--effort low` (default) is fine for summarization/boilerplate — fast and
  cheap. Bump to `--effort medium` only if low-effort output is clearly weak
  for the specific task.
- Omit `--model` to use the default (`gpt-oss:20b`); pass a different model
  name if the user has pulled something else they prefer for this.
- The script prints the model's reply to stdout and exits non-zero with a
  clear error if Ollama isn't running or the model isn't pulled — check for
  that and fall back to doing the task directly rather than retrying blindly.
- **For genuinely large input** (a multi-thousand-line log, a big build
  output): grep/filter it down to the relevant lines *before* delegating,
  don't paste the whole thing. The script refuses input over ~28K estimated
  tokens rather than silently overflowing the context window — confirmed in
  testing that an oversized prompt doesn't error, it just produces a
  generic/confused non-answer with no indication anything went wrong. A
  ~340KB log summarized directly failed silently this way; grepping it down
  to the ERROR/WARN lines first (488 of ~4000 lines) worked cleanly and
  correctly surfaced the two real errors buried in the noise.

3. Treat the output as a first draft. Skim it before using it verbatim —
   local models are meaningfully weaker than Claude, so spot-check anything
   that will be committed or shown to the user, and redo it directly if it's
   wrong rather than trying to salvage a bad local generation.

## Notes

- This is a single stateless call, not a conversation — there is no session
  or history to manage. For a task that needs multiple turns of local
  back-and-forth or unattended tool use (running commands, editing files),
  use OpenCode instead (see `docs/opencode-setup.md` in the foreman repo).
- If Ollama is not installed or not running, say so plainly and fall back to
  doing the task directly rather than silently spending more effort trying to
  fix the local setup mid-task.
