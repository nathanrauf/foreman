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

Ranked by how far each model has actually been verified, not by speed. That
ordering is deliberate and was corrected after the fact: a fast model that
silently produces broken output costs more than a slow one that works,
because catching it burns a review cycle and every failure this project hit
was silent. `foreman-recommend` sorts on the same principle.

All four numbers below come from the same benchmark, run the same way: a
realistic bug-fix task (find an unreachable branch in existing code, fix it
minimally, add regression tests, preserve the existing ones, update a
changelog), through Ollama, context 32768, `OLLAMA_NUM_PARALLEL=1`, KV
cache `q8_0`, one model resident at a time, graded by 15 objective checks
run independently of what the model claimed. Timings from different tasks
are not comparable and are not mixed here.

| | score | time | VRAM |
|---|---|---|---|
| `qwen3.6:35b-a3b` | **15/15** | **77s** | 23GB, offload |
| `qwen3.6:27b` | 15/15 | 242s | 18GB, offload |
| `gpt-oss:20b` | 14/15 | 36s | 13GB, fits |
| `devstral:24b` | 8/15 | 26s | 14GB, offload |

**`qwen3.6:35b-a3b` is the default and the best model tested.** `ollama
pull qwen3.6:35b-a3b`, 23.9GB. It is MoE: 35B total but only ~3B active per
token, which is why it beats the *dense* 27B of its own family by roughly
3x on speed while carrying *more* CPU offload (41%/59% against 25%/75%).
Perfect score, minimal fix, every original test preserved, three new
regression tests, changelog updated.

Read that comparison carefully, because it inverts the obvious assumption:
the "smaller" 27B is computationally the larger model and the slower one.
Prefer MoE when VRAM is tight.

An earlier version of this file said this model was llama.cpp-only. That
was stale and would have sent you to build a server for nothing. The
unsloth GGUF still works that way if you want manual CPU/GPU control, but
`--mlock` there pins ~19GB of RAM until you stop the server by hand, while
Ollama auto-unloads on idle.

**`gpt-oss:20b` is the small/fast model**, and the only one that fits
*entirely* in 16GB VRAM with no offload at all. 36s, more than twice as
fast as anything else, and it gets the central change right consistently.
Its weakness is the requirements *around* the code: it shorted a README on
one task, and on this one it fixed the bug correctly, added good tests, and
then **deleted an existing passing test** the task explicitly said to keep,
leaving a green suite that hides the loss. Good for quick mechanical work;
check its secondary deliverables every time.

Tested on a task past trivial (an 8-function module with JSON persistence,
validation, a real test suite, and a README), 2026-08-23: got the actual
code right. All 8 functions correct, 19 tests written that all pass under
independent verification. But the README it wrote didn't do what the task
asked (document each function's signature, behavior, and exceptions); it
just asserted that documentation existed elsewhere without providing it,
then reported the file set complete regardless. The core deliverable was
correct, one secondary requirement was silently shorted. Worth remembering
that "did the model complete the task" isn't a single yes/no; check every
requirement, not just the main one.

**`qwen3:8b`: fast, and not recommended despite it.** 5.2GB, ~8GB VRAM, no
offload, 28-39s/task, and the highest BFCL multi-turn score of the Qwen3
models checked. It was this project's default for exactly those reasons,
and that was a mistake. It's 3/3 only on trivial single-file
read-and-edit tasks. On the first moderately harder task it was given, it
abandoned the actual file edit after three failed attempts, wrote a test
file for an invented class that doesn't exist anywhere, syntax error
included, and reported no problem. Nothing in the run signaled failure.
Fine for trivial mechanical edits if speed matters; don't extrapolate past
that.

**`qwen3-coder:30b`.** Reliable (3/3) but not through this exact setup. It's
an 18GB MoE model that splits ~35% CPU / 65% GPU on a 16GB card at OpenCode's
context size, and the CPU-offload penalty compounds badly with OpenCode's
large prompt: it timed out completely through plain Ollama+OpenCode (180s+,
zero output). Two ways it does work: through llama.cpp's own server with
`--fit on` (see below, ~190s), or through a smaller, purpose-built tool loop
with a much lighter system prompt, where the offload penalty doesn't
compound against nearly as much prefill overhead. This is a harness-weight
problem, not a verdict on the model.

**`devstral:24b`: the case for not trusting trivial-task results.** 14.3GB,
dense 23.6B, Mistral's agentic coding model. It was rejected here early for
narrating tool calls instead of making them, then found to be a harness
artifact: retested with an `AGENTS.md` present it tool-called cleanly in
38s. So it was promoted back.

Then it met its first realistic task and **did nothing at all**. One line
of output, "Let me help you with that. I'll look at pricing.py to find the
bug first," zero tool calls, every file byte-identical afterward, exit code
0. It scored 8/15, which is exactly what the untouched starting state
scores.

Two lessons, both expensive to learn twice. A rejection is only as good as
the harness configuration it was measured under, so recheck disqualified
models when the harness changes. And a trivial-task pass predicts nothing
about real work: this model went from a clean 2/2 read-and-edit to
producing literally zero output on a task one step harder.

## The harness itself costs time: OpenCode vs Pi

The agent harness is not a neutral wrapper around the model. It decides how
many tokens the model must read before it can start working, and on a model
doing CPU offload that prefill is expensive.

[Pi](https://github.com/earendil-works/pi) is a CLI coding agent with a
deliberately small four-tool core (read, write, edit, bash) against
OpenCode's heavier system prompt and larger tool schema. Same task, same
model (`qwen3.6:35b-a3b`), same `AGENTS.md`, model unloaded before every run,
three runs each, graded by the same 15 checks:

| | run 1 | run 2 | run 3 | mean | score |
|---|---|---|---|---|---|
| Pi | 52s | 54s | 52s | **52.7s** | 3/3 at 15/15 |
| OpenCode | 74s | 74s | 83s | 77.0s | 3/3 at 15/15 |

**Pi is about 32% faster for identical output**, consistently enough that the
variance doesn't threaten the conclusion. Neither harness lost quality; both
produced a minimal fix, kept every existing test, added regression tests and
updated the changelog on all three runs.

This is the same effect that made `qwen3-coder:30b` look broken. It timed out
entirely under OpenCode while working under a lighter loop, which was
recorded here as a model problem when it was really a prompt-weight problem.
A smaller harness prompt is worth real time on any model that spills onto the
CPU.

What it costs you: Pi needs Node 18+, while OpenCode ships as a standalone
binary with no runtime dependency at all. On a machine still running an old
Node, that's a system-wide upgrade before you can even try it.

Pi points at Ollama through `~/.pi/agent/models.json`:

```json
{
  "providers": {
    "ollama": {
      "baseUrl": "http://localhost:11434/v1",
      "api": "openai-completions",
      "apiKey": "ollama",
      "compat": {
        "supportsDeveloperRole": false,
        "supportsReasoningEffort": false
      },
      "models": [
        { "id": "qwen3.6:35b-a3b", "reasoning": true }
      ]
    }
  }
}
```

The `apiKey` is a placeholder Ollama ignores, but Pi hides models with no
credential, so it has to be present. The `compat` block matters: some
OpenAI-compatible servers reject the `developer` role and `reasoning_effort`
that Pi sends to reasoning-capable models.

Run it non-interactively the same way, attaching the task rather than
inlining it:

```bash
pi -p --provider ollama --model qwen3.6:35b-a3b -a -- @task.txt "Follow the attached task description."
```

Worth measuring on your own hardware before switching. This is one task, one
model, one machine, and the gap comes from prefill, so it should shrink on a
model that fits entirely in VRAM and grow on one that offloads harder.

## Does the runtime cause tool-calling failures?

Sometimes, and not in the direction the common advice suggests. There's
real evidence here for "Ollama's chat templates break tool-calling":
`qwen3-coder:30b` times out entirely through Ollama and works through
llama.cpp. But tested directly, with the same model, same Q4_K_M quant
(the GGUF blob Ollama itself downloaded), same task, same `AGENTS.md`, and
a verified-clean GPU:

| `devstral:24b` | Ollama | llama.cpp |
|---|---|---|
| Real tool calls | 2 | 0 |
| File actually modified | yes | no |
| Time | 38s | 239s |

llama.cpp produced exactly the narration failure the advice blames on
Ollama: XML pseudo-markup instead of a tool call, file untouched. Devstral
uses Mistral's tool-call format and Ollama ships a curated template for it,
while llama.cpp fell back to the template embedded in the GGUF.

So the runtime genuinely matters, per model, in both directions. Neither is
categorically better. If a capable model narrates tool calls instead of
making them, try the other runtime before concluding anything about the
model, and check `AGENTS.md` is present first, since that fixes the same
symptom more cheaply.

One practical note: llama.cpp can load the GGUF blob Ollama already pulled,
so testing both ways costs no extra download. Find it via the manifest in
`$OLLAMA_MODELS/manifests/registry.ollama.ai/library/<model>/<tag>`.

**Rejected: the Qwen2.5 generation**, for tool-calling through this stack
specifically (and see the warning above: these were measured under the same
old configuration that wrongly condemned devstral, so they deserve a
recheck before being treated as settled):

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

`--mlock` and `--no-mmap` are deprecated in recent llama.cpp builds in
favor of `--load-mode mlock` / `--load-mode mmap`. They're still accepted
as aliases and still apply, but they emit a deprecation warning at startup
and will presumably stop working eventually.

One thing to check before trusting a timing measurement: confirm nothing
else is holding VRAM first (`nvidia-smi`, and `ollama ps` won't always tell
you, an Ollama `llama-server.exe` can linger on the GPU after its model
shows as unloaded). `--fit on` balances against whatever VRAM is actually
free at startup, so a leftover process silently shifts more of the model
onto the CPU and every number you measure afterward is wrong.

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
