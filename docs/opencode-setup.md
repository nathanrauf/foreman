# Running tasks: OpenCode + a local model

This is the actual execution pattern this project uses, not an alternative
to something else. Claude picks the model (`foreman-recommend`) and the
task; OpenCode runs it against Ollama.

## Does Claude Code itself work with Ollama directly?

This changed since it was first checked, and is worth re-testing again in
the future: Ollama v0.14.0+ (January 2026) added a native Anthropic
Messages API endpoint (`/v1/messages`). Pointing `ANTHROPIC_BASE_URL` at a
local Ollama instance, with `ANTHROPIC_AUTH_TOKEN` set to any placeholder
value, now gets a real response at the raw protocol level. Confirmed with a
direct API call on this machine (Ollama 0.32.7): a normal Anthropic-shaped
response, thinking block included. That supersedes the older unofficial
`ollama launch claude` gateway hack, which used to produce nothing past an
internal title-generation call.

Re-tested through the actual `claude` CLI itself, not just the raw API,
and it still isn't usable for real work. Same task both models handle
fine through OpenCode (read a file, report its contents), same
`ANTHROPIC_BASE_URL` setup, run 2026-08-23:
- `qwen3-coder:30b` narrated the tool call as literal text
  (`<function=Read>...`) instead of invoking it.
- `qwen3:8b` fabricated a permission-restriction excuse and never
  attempted a tool call at all.

Same failure family documented throughout this project, just against
Claude Code's own system prompt and tool schema instead of OpenCode's.
Not adopted as an execution path for that reason. OpenCode remains the
harness this project actually uses.

## Setup

OpenCode's own docs hedge on Windows ("in progress", recommends WSL), but in
practice, confirmed by community reports and by testing on this project's
own machine (RTX 5070 Ti, 16GB VRAM, Windows 11, no WSL), the CLI works
natively.

1. Install: `npm i -g opencode-ai@latest`
2. Add `opencode.json` to your project root:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama (local)",
      "options": { "baseURL": "http://localhost:11434/v1" },
      "models": {
        "qwen3:8b": { "name": "qwen3 8b" },
        "gpt-oss:20b": { "name": "gpt-oss 20b" }
      }
    }
  }
}
```

Without this file, OpenCode has no Ollama provider configured and fails with
an opaque `UnknownError` rather than a clear message. If that happens,
check this file exists.

3. Run: `opencode run "Follow the attached task." -f task.txt -m ollama/qwen3:8b --dir /path/to/project --auto`
   (write the real task to `task.txt` first; see the note on Windows below
   for why the task shouldn't go directly in the message string)

## Required: two Ollama settings, or you can crash your machine

This is the important part, not a nice-to-have. OpenCode requests a large
context window by default (65536 tokens) and fires **parallel tool calls**.
Ollama sizes its KV cache as `num_ctx × num_parallel`, so combined with a
model that's already spilling from VRAM into system RAM (anything larger
than your card's VRAM), this can multiply memory demand enough to hard-lock
the machine. This happened during testing for this project and needed a
hard reset.

Set both before running `ollama serve`:

```bash
setx OLLAMA_NUM_PARALLEL 1        # caps concurrent slots, the actual fix
setx OLLAMA_KV_CACHE_TYPE q8_0    # halves KV cache memory, ~no quality loss
```

## Tool-call reliability: add an AGENTS.md

Local models tend to narrate a corrected tool call as text instead of
re-invoking it after a tool error, across every model tried. Add an
`AGENTS.md` to your project root:

```markdown
# Agent Instructions

You MUST accomplish tasks by actually calling the provided tools; never describe,
narrate, or print what a tool call would look like as text or JSON. This applies
especially after a tool call fails: retry by actually invoking the tool again with
corrected arguments, not by writing out the corrected call as text.
```

This took both `gpt-oss:20b` and `qwen3-coder:30b` from unreliable (failing
to actually apply an edit after a retry) to a clean 3/3 across repeated test
runs, each independently verified by checking the file, not by trusting the
model's own "done" message.

## The gap `--auto` opens, and how this project covers it

`--auto` is required for unattended use, since nobody's there to click
approve. But OpenCode's own loop-protection (`doom_loop`: asks for approval
if the same tool call repeats 3 times identically) defaults to `ask`, and
`--auto` auto-approves anything that would otherwise ask. Run unattended,
that protection is bypassed by design, not by accident.

What actually covers this in practice: every invocation runs under an
external process timeout (`timeout N opencode run ...`), which is a real,
if blunt, cap on "runs forever." It doesn't catch "keeps retrying a failing
call many times within the time budget." That's an accepted, understood
gap, not an oversight. If that matters more for a given task than the
simplicity of this setup, weigh that trade-off before relying on it.

## How Claude actually drives this

Launch with a timeout, review the result, decide what's next: the same loop
used to validate every model in this repo.

**On Windows, put the task in a file and attach it; don't inline it in the
message string.** `opencode`'s npm install resolves to a `.cmd` batch-file
wrapper, and cmd.exe's batch-argument handling silently drops or reorders
CLI flags when an argument contains an embedded newline. A real multi-line
task description does exactly that, and the practical effect is `-m` gets
ignored without any error: the run silently falls back to whatever model
`opencode.json`'s top-level `model` is set to. Confirmed on this machine:
a real task run this way silently used `qwen2.5-coder:14b`, a model this
project's own testing already rejected for tool-calling, instead of the
model actually requested, and produced unrelated, wrong output narrated as
text instead of real tool calls. It still exited 0. Nothing about the run
signaled that the wrong model had been used.

The fix is to keep the message argument short and single-line, and pass the
real task through `-f`:

```bash
timeout 240 opencode run "Follow the attached task description." \
  -f task.txt -m ollama/<model> --dir /path/to/project --auto
```

This isn't Windows-only paranoia: writing the task to a file rather than
inlining it is already `foreman-errand`'s convention for the same category
of shell-quoting reasons, so this just applies the same practice here.

Then read the diff, run any relevant tests, and only report success once
that's independently confirmed. Every "the model says it's done" claim in
this project's own testing that wasn't independently checked turned out
wrong at least once, including a resumed session that silently duplicated
content across 5 files while reporting success, and a model that invented a
plausible excuse for a real `pip` failure and claimed completion anyway.
Trusting the model's own report is the single most common way this pattern
actually fails.

## Model notes

**`qwen3:8b` is the current default.** 5.2GB download, ~8GB VRAM, no CPU
offload. Found via BFCL (Berkeley Function Calling Leaderboard) multi-turn
tool-calling data, where it scored *higher* than 14B and 30B models from the
same family, non-monotonic with size. Confirmed 3/3 on trivial single-file
read-and-edit tasks through OpenCode, 28-39 seconds per task, the fastest
and most reliable model found in this project's testing at that task size.

On a moderately harder task (edit an existing file to add a function, write
a real test suite, add input validation), it failed differently and worse:
it abandoned the actual file edit after three failed attempts, then wrote a
test file for an invented class that doesn't exist anywhere, with a syntax
error, and reported no problem. Treat "3/3" as validated for simple tasks
specifically, not as a general reliability guarantee. Always independently
verify regardless of how the task went.

**`gpt-oss:20b`.** 13GB, fits fully in 16GB VRAM, reliable, ~110s/task.
Solid fallback if `qwen3:8b` doesn't suit a specific task.

Tested on a task past trivial (an 8-function module with JSON persistence,
validation, a real test suite, and a README), 2026-08-23: got the actual
code right. All 8 functions correct, 19 tests written that all pass under
independent verification. But the README it wrote didn't do what the task
asked (document each function's signature, behavior, and exceptions); it
just asserted that documentation existed elsewhere without providing it,
then reported the file set complete regardless. A different failure shape
than `qwen3:8b`'s outright breakage above: the core deliverable was
correct, one secondary requirement was silently shorted. Worth remembering
that "did the model complete the task" isn't a single yes/no; check every
requirement, not just the main one.

**`qwen3-coder:30b`.** Reliable (3/3) but not through this exact setup. It's
an 18GB MoE model that splits ~35% CPU / 65% GPU on a 16GB card at OpenCode's
context size, and the CPU-offload penalty compounds badly with OpenCode's
large prompt: it timed out completely through plain Ollama+OpenCode (180s+,
zero output). Two ways it does work: through llama.cpp's own server with
`--fit on` (see below, ~190s), or through a smaller, purpose-built tool loop
with a much lighter system prompt, where the offload penalty doesn't
compound against nearly as much prefill overhead. This is a harness-weight
problem, not a verdict on the model.

**Rejected: the Qwen2.5 generation**, for tool-calling through this stack
specifically:

- `qwen2.5-coder:14b`: 0/2 on tool-calling, confirmed via its own Hugging
  Face model card, which doesn't mention tool/function calling at all. (A
  "Qwen2.5-Coder supports function calling" claim exists, but describes
  Alibaba's *hosted* Model Studio API adding tool-calling as a
  platform-level feature independent of the model's own training. It
  doesn't apply to the raw open weights run through Ollama.)
- `qwen2.5:14b-instruct`: genuinely inconsistent. One run completed
  correctly with legitimate tool calls and sensible self-correction after a
  failed edit; an otherwise identical run hung with zero output for 90+
  seconds. A raw API call with a minimal tool set worked instantly, so the
  model can format tool calls correctly, and the inconsistency shows up
  specifically under OpenCode's full prompt and tool-schema load.

## llama.cpp as an alternative backend

For models that need offload (`qwen3-coder:30b`, or anything bigger than
your VRAM), llama.cpp's own server gives finer control over the CPU/GPU
split than Ollama's automatic decision, via `--fit on` (auto-balances,
adapts if the context size changes) or manual `--n-cpu-moe N` tuning.

You don't need to re-download a model already pulled through Ollama.
Ollama's storage is just GGUF blobs with a manifest on top, so llama.cpp can
read the same file directly (find it under Ollama's model directory,
matched to a tag via its manifest).

```
llama-server.exe -m <path-to-gguf> \
  --fit on --fit-ctx 65536 --fit-target 256 \
  -np 1 -fa on -ctk q8_0 -ctv q8_0 \
  --host 127.0.0.1 --port 8033
```

`-np 1` matters here for the same reason `OLLAMA_NUM_PARALLEL=1` does above:
it caps concurrent slots so the KV cache doesn't multiply.

Point `opencode.json`'s `baseURL` at `http://127.0.0.1:8033/v1` instead of
Ollama's port to use it.

Validated this way: `qwen3-coder:30b` (~190s/task, up from a complete
timeout on plain Ollama) and `Qwen3.6-35B-A3B` (a MoE, hybrid
attention/recurrent model not on Ollama's library at all, manually
downloaded as a GGUF, 3/3 through OpenCode, 43-55s/task). One real caution:
`--mlock` (used for the full tuned config on `Qwen3.6`) holds a large chunk
of memory in physical RAM for as long as the server runs, and doesn't
auto-unload on idle the way Ollama does. Stop the server manually when
done, or it keeps holding that memory indefinitely.
