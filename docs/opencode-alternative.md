# Alternative: OpenCode + Ollama

The two skills in this repo are a deliberately minimal harness, built because pointing
Claude Code itself at Ollama doesn't work (see below). If you want a more capable local
coding agent than the custom scripts here provide, [OpenCode](https://opencode.ai) is a
purpose-built alternative that we tested and can recommend, with caveats.

## Does Claude Code work with Ollama directly?

No. Claude Code only supports Anthropic's own infrastructure (the Anthropic API,
Bedrock, Vertex, Foundry). Its docs are explicit that routing to non-Claude models
through a gateway is unsupported.

Ollama does ship an unofficial integration (`ollama launch claude`) that spins up a
gateway and points a real `claude` process at it. In testing, this never produced a
working response across several invocation styles — it got as far as an internal
title-generation call and then produced nothing for the actual prompt. We don't
recommend pursuing it further.

## OpenCode works natively on Windows, no WSL required

[OpenCode](https://github.com/sst/opencode) is a purpose-built open-source coding
agent with real Ollama support. OpenCode's own docs hedge on Windows ("in progress",
recommends WSL), but in practice — confirmed both by community reports and by testing
on this project's own machine (RTX 5070 Ti, 16GB VRAM, Windows 11, no WSL) — the CLI
works natively via `npm i -g opencode-ai`.

### Setup

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
        "gpt-oss:20b": { "name": "gpt-oss 20b" },
        "qwen3-coder:30b": { "name": "qwen3-coder 30b" }
      }
    }
  }
}
```

Without this file, OpenCode has no Ollama provider configured and fails with an opaque
`UnknownError` rather than a clear message — if you hit that, check this file exists.

3. Run: `opencode run "your task" -m ollama/gpt-oss:20b --dir /path/to/project`

### Required: two Ollama settings, or you can crash your machine

This is the important part. OpenCode requests a large context window by default
(65536 tokens) and fires **parallel tool calls**. Ollama sizes its KV cache as
`num_ctx × num_parallel` — combined with a model that's already spilling from VRAM
into system RAM (any model larger than your card's VRAM), this can multiply memory
demand enough to hard-lock the machine. This happened during testing for this project
and required a hard reset.

Set both before running `ollama serve`:

```bash
setx OLLAMA_NUM_PARALLEL 1        # caps concurrent slots — the actual fix
setx OLLAMA_KV_CACHE_TYPE q8_0    # halves KV cache memory, ~no quality loss
```

With both set, `qwen3-coder:30b` (30B MoE, ~18GB, splits ~35% CPU / 65% GPU on a 16GB
card at this context) ran stably with headroom to spare — verified with live
`nvidia-smi` and free-RAM monitoring during the test, not just a single successful run.

### Tool-call reliability: add an AGENTS.md

Local models tend to narrate a corrected tool call as text instead of re-invoking it
after a tool error — the same failure mode this repo's own `ollama-agent-head` skill
had to work around. Add an `AGENTS.md` to your project root:

```markdown
# Agent Instructions

You MUST accomplish tasks by actually calling the provided tools — never describe,
narrate, or print what a tool call would look like as text or JSON. This applies
especially after a tool call fails: retry by actually invoking the tool again with
corrected arguments, not by writing out the corrected call as text.
```

This took both `gpt-oss:20b` and `qwen3-coder:30b` from unreliable (failing to
actually apply an edit after a retry) to a clean 3/3 across repeated test runs,
each independently verified by checking the file, not by trusting the model's own
"done" message.

### Model notes

**`gpt-oss:20b` is the recommended default for OpenCode specifically** — not because
it's the more capable model, but because it's the only one of the two that's actually
fast enough through this harness. Timed head-to-head on an identical trivial task
(add one function to a file): `gpt-oss:20b` completed correctly in 110 seconds;
`qwen3-coder:30b` produced zero output and hadn't finished after 180 seconds.

This is a harness-weight problem, not a verdict on the model. `qwen3-coder:30b` splits
~35% CPU / 65% GPU on a 16GB card at OpenCode's default 65536-token context, and
CPU-offloaded inference is much slower per token. OpenCode's system prompt and full
tool schema are large enough that this penalty compounds badly. In this repo's own
leaner `ollama-agent-head` skill — a much smaller system prompt, fewer tools — the
same model completed multi-step tasks in 15–60 seconds and was *more* efficient than
`gpt-oss:20b`. Match the model to the harness: `qwen3-coder:30b` for the lean custom
skill, `gpt-oss:20b` for OpenCode.

**Rejected: the Qwen2.5 generation**, for tool-calling through this stack specifically:

- `qwen2.5-coder:14b` — 0/2 on tool-calling, confirmed via its own Hugging Face model
  card, which doesn't mention tool/function calling at all. (A "Qwen2.5-Coder supports
  function calling" claim exists, but it's describing Alibaba's *hosted* Model Studio
  API adding tool-calling as a platform-level feature independent of the underlying
  model's training — it doesn't apply to the raw open weights run through Ollama.)
- `qwen2.5:14b-instruct` — genuinely inconsistent: one run completed correctly with
  legitimate tool calls and sensible self-correction after a failed edit; an otherwise
  identical run hung with zero output for 90+ seconds. A raw API call with a minimal
  tool set worked instantly, so the model can format tool calls correctly — the
  inconsistency shows up specifically under OpenCode's full prompt and tool-schema
  load. Not recommended until that's better understood.
