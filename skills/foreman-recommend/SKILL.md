---
name: foreman-recommend
description: This skill should be used when the user asks "what model should I use for local Ollama/OpenCode work", "recommend a local model", "which model fits my GPU", or when setting up ollama-agent-head/OpenCode for the first time on a machine and no validated model is known yet — it shortlists candidates by hardware fit and real tool-calling benchmark data instead of guessing from marketing claims or blog posts.
---

# Foreman: Model Recommendation

Don't make the user manually try a bunch of local models to find one that
works — that's real time and real friction, and it's exactly the kind of
thing this skill exists to do instead. Shortlist candidates using actual
data, then verify the top pick(s) empirically before recommending anything.

## Why this exists

Model quality claims are unreliable in ways that cost real time to
discover by hand: a model can be marketed as "great at tool calling" and
have zero documented tool-calling training (`qwen2.5-coder`), or work
inconsistently through one specific serving stack despite working fine at
the raw API level (`qwen2.5:14b-instruct`). Bigger isn't reliably better
either — in real testing, an 8B model outscored 14B and 30B models from the
same family on multi-turn tool-use accuracy. None of this is discoverable
from a model's name or parameter count alone.

## Stage 1: shortlist (automated, this script)

```bash
python "$HOME/.claude/skills/foreman-recommend/scripts/recommend_model.py"
```

Ranks candidates from a curated list (`CANDIDATES` in the script) by:
1. Whether they fit in available VRAM without CPU offload (offload has
   repeatedly correlated with severe slowdowns under heavy harnesses like
   OpenCode — sometimes complete timeouts).
2. BFCL (Berkeley Function Calling Leaderboard) multi-turn tool-calling
   accuracy, fetched live from `HuanzhiMao/BFCL-Result` on GitHub — real
   benchmark data, not a claim.

Pass `--vram-gb N` to override auto-detection (via `nvidia-smi`) if needed,
e.g. for planning a recommendation for hardware Claude isn't currently
running on.

**Known limitation:** BFCL scores a model's trained capability, generally
via a hosted/full-precision endpoint — not a specific GGUF quantization
running through Ollama's or llama.cpp's chat-template parsing. That gap is
exactly what broke `qwen2.5-coder` despite it nominally supporting function
calling. A high BFCL score is a strong reason to test a candidate; it is
not itself sufficient reason to recommend one.

## Stage 2: verify empirically (required before recommending)

Never recommend a shortlisted model without actually testing it. The
protocol, run against the top 1-2 candidates from stage 1:

1. Pull it (`ollama pull <tag>`).
2. Sanity-check raw generation (`/api/chat`, no tools) — confirms the model
   loads and responds at all.
3. Set up a scratch test directory with an `AGENTS.md` containing:
   > You MUST accomplish tasks by actually calling the provided tools —
   > never describe, narrate, or print what a tool call would look like as
   > text or JSON. This applies especially after a tool call fails: retry
   > by actually invoking the tool again with corrected arguments, not by
   > writing out the corrected call as text.

   (Without this, otherwise-capable models often narrate a tool call as
   text instead of invoking it, especially after a retry — a known failure
   mode, not evidence the model can't do the task.)
4. Run a real tool-calling task through the harness actually being used
   (OpenCode or `ollama-agent-head`) — e.g. "read file X, add a line to
   it" — **three times**, not once. A single success can be luck.
5. After each run, check the file directly. Do not trust the model's own
   "done"/"successfully edited" message — that has been wrong before while
   sounding completely confident.
6. Note the timing. A model that works but takes 3+ minutes for a trivial
   task is a different recommendation than one that takes 30 seconds, even
   at identical correctness — this has been the deciding factor between
   otherwise-similar candidates more than once.

Only recommend a model after it passes step 4-5 at least 3/3. Record the
result (model, VRAM footprint, timing, pass rate) back into
`CANDIDATES` in `recommend_model.py` and into project memory, so the next
recommendation run — on this machine or another — doesn't redo the work.

## Notes

- The candidate list is curated by hand, not auto-discovered — BFCL's model
  naming doesn't map cleanly to Ollama tags (e.g. `qwen3-30b-a3b-instruct-2507-FC`
  vs `qwen3-coder:30b`), so matches are approximate unless verified. Add new
  candidates as they're found and tested; don't assume an unlisted model is
  bad, just untested.
- This shortlists models already know to be *available* (Ollama library,
  known GGUF sources) for a given task type (agentic coding). It does not
  search the entire internet for "the best possible model" — that's a much
  larger, more open-ended problem than what's built here.
