---
name: foreman-recommend
description: This skill should be used when the user asks "what model should I use for local Ollama/OpenCode work", "recommend a local model", "which model fits my GPU", or when setting up OpenCode for the first time on a machine and no validated model is known yet. It shortlists candidates by hardware fit and real tool-calling benchmark data instead of guessing from marketing claims or blog posts.
---

# Foreman: Model Recommendation

Nobody should have to manually download and try a dozen local models to
find one that works. That's a real afternoon lost, and it's exactly the
job this skill exists to do instead: shortlist candidates from actual
data, then verify the top pick before recommending it to anyone.

## Why this exists

Model quality claims turn out to be unreliable in specific, costly ways.
A model can be marketed as "great at tool calling" while its own model
card never mentions tool-calling training at all (`qwen2.5-coder`), or it
can work fine at the raw API level and still fall apart the moment a real
harness's larger prompt and tool schema get involved (`qwen2.5:14b-instruct`).
Bigger isn't reliably better either: in real testing here, an 8B model
outscored 14B and 30B models from its own family on multi-turn tool-use
accuracy. None of that shows up in a model's name or parameter count. You
have to actually check.

## Stage 1: shortlist (automated, this script)

```bash
python "$HOME/.claude/skills/foreman-recommend/scripts/recommend_model.py"
```

Two tiers, both filtered to what actually fits the detected VRAM:

- **Known candidates**, ranked by **how far each has actually been
  verified**, then by measured speed, then by BFCL (Berkeley Function
  Calling Leaderboard) multi-turn tool-calling accuracy, fetched live from
  `HuanzhiMao/BFCL-Result` on GitHub. Real benchmark data, not a claim.

  Reliability outranking speed is deliberate, and it's a correction. This
  script originally sorted measured candidates by speed alone, which put a
  model on top that was both the fastest tested (28-39s/task, best BFCL
  score in its family) and the only one with a documented silent failure:
  on a task past trivial it skipped a file edit, wrote tests for a class
  that doesn't exist, and reported success. A model that produces broken
  output quickly is worse than a slower one that works, because the cost
  lands on the review cycle that has to catch it. Candidates now carry a
  `validated_at` tier (`moderate`, `moderate-partial`, `trivial`, or
  untested) plus a `documented_failure` flag, and both sort above speed.
- **Discovered candidates**, pulled live from the Hugging Face model API
  (search sorted by both downloads and recency, merged), with exact quant
  file sizes confirmed through HF's tree API rather than estimated from
  parameter count. This is what keeps the list from calcifying the moment
  a new model ships; it once surfaced a model uploaded the day before.

Pass `--vram-gb N` to override auto-detection (via `nvidia-smi`) for
planning a recommendation on hardware Claude isn't currently running on.
Pass `--no-discover` to skip the Hugging Face search and stick to the fast
known-candidate path.

**Known limitation:** BFCL scores a model's trained capability, generally
through a hosted or full-precision endpoint, not a specific GGUF
quantization running through Ollama's or llama.cpp's chat-template
parsing. That gap is exactly what broke `qwen2.5-coder` despite it
nominally supporting function calling. Discovered candidates skip BFCL
scoring entirely for the same reason: matching an arbitrary Hugging Face
repo name to a benchmark entry reliably enough to trust the score is
harder than it sounds, and a wrong match is worse than no score. A high
BFCL score is a good reason to test a candidate. It is never a substitute
for testing it.

## Stage 2: verify empirically (required before recommending)

Never recommend a shortlisted model without actually testing it. This is
not a formality; a model has failed this exact check after looking fine on
paper. The protocol, run against the top 1-2 candidates from stage 1:

1. Pull it (`ollama pull <tag>`, or download the GGUF for a `llamacpp`
   backend candidate).
2. Sanity-check raw generation (`/api/chat`, no tools). Confirms the model
   loads and responds at all before spending more time on it.
3. Set up a scratch test directory with an `AGENTS.md` containing:
   > You MUST accomplish tasks by actually calling the provided tools,
   > never describe, narrate, or print what a tool call would look like as
   > text or JSON. This applies especially after a tool call fails: retry
   > by actually invoking the tool again with corrected arguments, not by
   > writing out the corrected call as text.

   Without this, otherwise-capable models often narrate a tool call as
   text instead of invoking it, especially after a retry. It's a known
   failure mode, not evidence the model can't do the task, but it will
   look exactly like the model can't do the task until this is in place.
4. Run a real tool-calling task through OpenCode, e.g. "read file X, add a
   line to it," **three times**, not once. A single success can be luck.
5. After each run, check the file directly. Do not trust the model's own
   "done" or "successfully edited" message. That has been wrong before
   while sounding completely confident about it.
6. Note the timing. A model that works but takes three minutes for a
   trivial task is a different recommendation than one that takes thirty
   seconds, even at identical correctness. This has decided between
   otherwise-similar candidates more than once.
7. If there's time, try one task past trivial. A model that's 3/3 on
   "read a file and edit one line" has been wrong before on anything
   harder, silently and without complaint. Treat trivial-task success as
   validated for trivial tasks, not as a general guarantee.

Only recommend a model after it clears steps 4-5 at 3/3. Record the result
(model, VRAM footprint, timing, pass rate, and how far past trivial it was
actually tested) back into `CANDIDATES` in `recommend_model.py` and into
project memory, so the next recommendation run, on this machine or
another, doesn't have to redo the work.

## Notes

- The known-candidate list is curated by hand and takes real effort to
  extend: BFCL's model naming doesn't map cleanly to Ollama tags (compare
  `qwen3-30b-a3b-instruct-2507-FC` to `qwen3-coder:30b`), so matches are
  approximate unless verified. Discovered candidates fill the gap this
  leaves for anything not already known about; add a known-good result to
  `CANDIDATES` once it's been through stage 2, rather than leaving it to
  be rediscovered fresh every run.
- This shortlists models that are actually available right now (Ollama's
  library, live Hugging Face search) for a given task type, mainly
  agentic coding. It does not attempt to search the entire internet for
  some Platonic ideal of "the best possible model." That's a different,
  much larger problem than the one this solves.
