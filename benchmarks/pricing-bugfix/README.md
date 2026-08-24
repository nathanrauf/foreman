# pricing-bugfix benchmark

The shared task every model in `foreman-recommend`'s known list is timed and
scored on. Having one fixed task matters more than the task being clever:
the same model measured 666s on a greenfield task and 242s on this one, so
timings from different tasks say nothing when compared.

## The task

`pricing.py` has a tier-discount function whose `>= 500` branch is
unreachable, because the `>= 100` check sits above it and always returns
first. A $600 order gets 10% off instead of 20%.

It's deliberately shaped like real work rather than a puzzle:

- **The bug is in existing code**, not a blank file. The model has to read
  and understand before changing anything.
- **All six existing tests pass at the start.** Running the suite reveals
  nothing. The docstring states the intended tiers and the code contradicts
  it, so the model has to actually compare them.
- **There are secondary requirements**: keep the existing tests, add
  regression tests at the boundary, update the changelog. Models that get
  the central fix right often quietly drop these.
- **A minimal fix is requested**, so rewriting the module counts against it.

## Running it

Copy this directory somewhere scratch, point a model at `TASK.txt`, then
grade the result:

```bash
opencode run "Follow the attached task description." \
  -f TASK.txt -m ollama/<model> --dir <scratch> --auto

python grade.py <scratch>
```

`grade.py` runs 15 boolean checks against observable state, ignoring
whatever the model said it did: bug actually fixed, all four tier
boundaries still correct, original tests still present, new tests added
covering the boundary, the suite green when *the grader* runs it, changelog
updated in the right section, and untouched functions left alone.

Calibration: the unmodified starting state scores 8/15, and a correct
minimal fix scores 15/15.

## Keeping measurements comparable

Hold these fixed across every model in a comparison, and re-time the whole
set if you change any of them:

- Ollama, context 32768, `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`
- One model resident at a time, `ollama stop` between runs
- `nvidia-smi` checked clean first, since a leftover process holding VRAM
  silently shifts more of the model onto the CPU and invalidates the timing
- The same `AGENTS.md` and `TASK.txt`, byte for byte

## What this task shows about delegation itself

Worth knowing before using this task to justify the approach: **it is a task
you should not delegate.** Done directly it takes 5 tool calls and 571
characters of generated code. Describing it well enough for a model that
cannot see your conversation takes 988 characters, so delegating costs
about 73% more on Claude's side than just doing it.

That isn't an argument against the benchmark, which measures whether a
model can do real work correctly, and it does that well. It's a reminder
that model quality and delegation economics are separate questions. A small
surgical fix is the wrong shape for handing off, however good the model is.

## Results so far

| model | score | time | notes |
|---|---|---|---|
| `qwen3.6:35b-a3b` | 15/15 | 77s | MoE ~3B active. Minimal fix, all tests kept, 3 added |
| `qwen3.6:27b` | 15/15 | 242s | Dense 27.8B. Same quality, ~3x slower |
| `gpt-oss:20b` | 14/15 | 36s | Correct fix, then deleted an existing passing test |
| `devstral:24b` | 8/15 | 26s | No tool calls, no file changes, exit code 0 |

The two failures are the reason the grader exists. `gpt-oss:20b` left a
green test suite behind, and `devstral:24b` exited successfully having done
nothing at all. Neither is visible without checking the files.
