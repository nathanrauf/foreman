#!/usr/bin/env python3
"""Shortlist local models for agentic tool use, given this machine's hardware.

Two tiers of candidates, both filtered by real VRAM fit:

  KNOWN: hand-curated, each with either a BFCL (Berkeley Function Calling
  Leaderboard) tool-calling score or actual empirical test results from this
  project (or both). Fast, no network calls beyond BFCL/live speed data.

  DISCOVERED: live-queried from the Hugging Face Hub API (search sorted by
  downloads, and separately by recency, merged) so this doesn't go stale the
  moment a new model ships. Each discovered candidate's exact quantized file
  size is fetched from HF's tree API, not guessed, to confirm VRAM fit.
  Discovered candidates are NOT auto-scored against BFCL (naming is too
  inconsistent to match reliably, and attaching a benchmark score to the
  wrong model would be worse than no score) and are NOT assumed to work;
  they are purely "found, and it fits," nothing more.

Stage 2, required for anything from either tier before recommending it for
real use: empirical verification. Pull it, run the standard tool-calling
test (with an AGENTS.md telling the model to actually call tools rather than
narrate them) three times through the harness actually being used, and check
the file changed correctly each time; do not trust the model's own "done"
message. This has caught real failures that looked fine on paper (KNOWN
entries below record several). See the skill's SKILL.md for the full
protocol.

Usage:
    python recommend_model.py [--vram-gb N] [--headroom-gb N] [--no-discover]
                               [--search TERM ...] [--discover-limit N]
"""
import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

BFCL_BASE = "https://raw.githubusercontent.com/HuanzhiMao/BFCL-Result/main/2025-12-16/score"
HF_API = "https://huggingface.co/api/models"
OLLAMA_LIBRARY = "https://ollama.com/library"
OLLAMA_REGISTRY = "https://registry.ollama.ai/v2/library"

# Ollama tags to try per model size, best quality first. Ollama's default tag
# for a size is usually Q4_K_M already; the explicit ones are tried first so
# the size lookup is unambiguous.
OLLAMA_TAG_SUFFIXES = ["-q4_K_M", ""]

# Tags to try for models whose library page lists no parameter-size badges.
# Some families tag by quantization only (glm-4.7-flash publishes q4_K_M,
# q8_0, bf16 and no 30b tag), and building tags purely from size labels made
# every one of them invisible: the parser saw the model and its tool-calling
# capability, found no sizes, and silently skipped it. That hid a whole
# family of current tool-capable models from discovery.
OLLAMA_SIZELESS_TAGS = ["q4_K_M", "latest"]

# Don't bother pulling manifests for models far too large for any consumer
# card; keeps the number of registry round-trips sane.
OLLAMA_MAX_PARAMS_B = 40

# How far past the VRAM budget a candidate may go and still be worth listing,
# flagged as needing CPU offload. Excluding these outright was wrong: the
# best-verified model this project has needs offload (a 22GB model on a 16GB
# card) and was therefore invisible to discovery while simultaneously being
# ranked first in the known list. Offload is a real cost, not a disqualifier,
# and it's a cost that grows with context length rather than being fixed, so
# the listing says so instead of hiding the candidate.
OFFLOAD_BUDGET_MULTIPLIER = 1.5

# Reasonable default quant preference order, balancing quality against size;
# the first one found in a repo's file list that fits the VRAM budget is used.
QUANT_PREFERENCE = ["Q4_K_M", "Q4_K_S", "IQ4_XS", "Q4_0", "Q5_K_M", "Q3_K_M"]

# Search terms used for HF discovery. Biased toward instruction-tuned and
# coding-oriented models, since that's this skill's actual use case. A
# generic "GGUF" search surfaces plenty of chat/roleplay models that aren't
# relevant here. Extend/adjust as the space shifts.
DISCOVERY_SEARCH_TERMS = ["instruct GGUF", "coder GGUF", "agent GGUF"]

# How far past trivial a model has actually been verified. Ranking treats this
# as more important than speed, because a fast model that silently produces
# broken output costs more than a slow one that works: catching it burns a
# review cycle, and the failures this project hit were all silent (the model
# reported success either way).
#
#   "moderate"         passed a real multi-function task (module + tests +
#                      docs), every named requirement met, independently
#                      verified.
#   "moderate-partial" got the core deliverable right, but silently shorted a
#                      named secondary requirement while reporting success.
#   "trivial"          only ever verified on single-file read-and-edit tasks.
#   None               not empirically tested here at all.
VALIDATION_RANK = {"moderate": 3, "moderate-partial": 2, "trivial": 1, None: 0}

# Mixture-of-Experts models activate only a fraction of their weights per
# token, so generation speed tracks ACTIVE parameters while capability tracks
# total. Ignoring the distinction is actively misleading: a dense 27.8B model
# measured here was slower than a 35B MoE with ~3B active, because the dense
# one does roughly 9x the arithmetic per token despite the smaller headline
# number. Ranking on total parameters alone would call the dense model the
# bigger, better one and be badly wrong about what it costs to run.
#
# Ollama's metadata doesn't expose expert counts, so this is recovered from
# naming conventions (Qwen's "A3B" means Active 3B) and recorded by hand for
# known models. Absent either, active params are unknown, not assumed equal
# to total.
MOE_ACTIVE_PARAMS_RE = re.compile(r"[-_]a(\d+(?:\.\d+)?)b\b", re.I)


def parse_active_params_b(name):
    """Active parameter count in billions from an MoE naming convention, or
    None if the name doesn't declare one."""
    m = MOE_ACTIVE_PARAMS_RE.search(name)
    return float(m.group(1)) if m else None

# measured_seconds_per_task is only comparable when every entry is timed on
# the SAME task under the same conditions. The current numbers are all from
# one realistic bug-fix task (find an unreachable branch in existing code,
# fix it minimally, add regression tests, preserve existing tests, update a
# changelog), Ollama, context 32768, OLLAMA_NUM_PARALLEL=1, KV cache q8_0,
# one model resident at a time. Re-time the whole set when changing the task
# rather than mixing numbers from different ones; the same model measured
# 666s on a greenfield task and 242s here.
#
# tag -> approx VRAM at Q4-class quantization (GB), BFCL result-dir name (or
# None if not covered), acquisition backend, how far it's actually been
# validated, whether it has a documented hard failure, and notes carrying
# forward what's already known. backend "ollama" means `ollama pull <tag>`;
# "llamacpp" means a manual GGUF download run through llama.cpp's own server
# (needed for models not published to Ollama's library, or that need
# --fit-style manual CPU/GPU tuning Ollama doesn't expose).
KNOWN_CANDIDATES = {
    "qwen3:8b": {
        "approx_vram_gb": 8, "active_params_b": 8.0, "bfcl_path": "Qwen_Qwen3-8B-FC", "backend": "ollama",
        "measured_seconds_per_task": 33,
        "validated_at": "trivial", "documented_failure": True,
        "notes": "Dense. The fastest candidate found (28-39s/task, 3/3) but ONLY on trivial single-file read-and-edit tasks. On the first moderately harder task it was given it failed outright and silently: skipped the actual file edit after three failed attempts, wrote a test file for a class that doesn't exist anywhere, syntax error included, and reported no problem. Speed is not the reason to pick a model; this is why validation tier outranks it here.",
    },
    "qwen3:14b": {
        "approx_vram_gb": 10, "active_params_b": 14.0, "bfcl_path": "qwen3-14b-FC", "backend": "ollama",
        "validated_at": None, "documented_failure": False,
        "notes": "Dense. Lower BFCL multi-turn score than qwen3:8b despite being larger; not yet empirically tested.",
    },
    "qwen3:32b": {
        "approx_vram_gb": 20, "active_params_b": 32.0, "bfcl_path": "Qwen_Qwen3-32B-FC", "backend": "ollama",
        "validated_at": None, "documented_failure": False,
        "notes": "Dense. Highest BFCL score of the Qwen3 family checked, but needs CPU offload on a 16GB card. Not yet empirically tested; offload previously correlated with severe slowdowns under OpenCode.",
    },
    "qwen3-coder:30b": {
        "approx_vram_gb": 19, "active_params_b": 3.3, "bfcl_path": "qwen3-30b-a3b-instruct-2507-FC", "backend": "ollama",
        "measured_seconds_per_task": None,  # timed out (>180s) via OpenCode+Ollama; 190s via OpenCode+llama.cpp+--fit
        "validated_at": "trivial", "documented_failure": False,
        "notes": "MoE ~3.3B active params. BFCL path is the closest available match (base instruct, not the coder fine-tune), approximate. Validated reliable (3/3) on trivial tasks but needs CPU offload on 16GB; times out through OpenCode's heavy prompt over plain Ollama, but works via llama.cpp with --fit (see docs/opencode-setup.md).",
    },
    "gpt-oss:20b": {
        "approx_vram_gb": 13, "active_params_b": 3.6, "bfcl_path": None, "backend": "ollama",
        "measured_seconds_per_task": 36,
        "validated_at": "moderate-partial", "documented_failure": False,
        "notes": "Not in BFCL's coverage. MoE ~3.6B active, the only model here that fits ENTIRELY in 16GB VRAM with no CPU offload, and by far the fastest (36s on the realistic bug-fix task). Gets the code right consistently. Its recurring failure is with the requirements AROUND the code: on a module task it silently shorted the README, asserting docs existed rather than writing them; on the bug-fix task it scored 14/15 by correctly fixing the bug and adding tests, then DELETING an existing passing test the task explicitly said to preserve, leaving a green suite that hides the loss. Fast and reliable for the central change, needs its secondary deliverables checked every time.",
    },
    "qwen3.6:35b-a3b": {
        "approx_vram_gb": 23, "bfcl_path": None, "backend": "ollama",
        "active_params_b": 3.0,
        "measured_seconds_per_task": 77,
        "validated_at": "moderate", "documented_failure": False,
        "notes": "MoE: 35B total but only ~3B active per token, which is why it beats the dense 27B of its own family on speed by ~3x while carrying MORE CPU offload (41%/59% vs 25%/75% on a 16GB card). BEST RESULT MEASURED HERE: 15/15 objective checks on a realistic bug-fix task (find an unreachable branch, fix minimally, add regression tests, preserve existing tests, update a changelog) in 77s, against 242s for the dense 27B on the identical task. Also 3/3 and a clean sweep of an earlier module task. AVAILABLE VIA PLAIN `ollama pull qwen3.6:35b-a3b` (23.9GB): this entry previously said llamacpp-only, which was stale and would have sent a new user to build a llama.cpp server for no reason. The unsloth GGUF (Qwen3.6-35B-A3B-UD-Q4_K_M, 22.1GB) still works via llama.cpp with `--fit on --fit-ctx 65536 --fit-target 256 -np 1 -fa on -ctk/-ctv q8_0` if you want manual CPU/GPU control, but note --mlock there holds ~19GB of RAM until the server is stopped by hand, whereas Ollama auto-unloads.",
    },
    "qwen3.6:27b": {
        "approx_vram_gb": 18, "bfcl_path": None, "backend": "ollama",
        "active_params_b": 27.8,
        "measured_seconds_per_task": 242,
        "validated_at": "moderate", "documented_failure": False,
        "notes": "DENSE 27.8B (all parameters active per token), so it is computationally the largest model here despite the smaller headline number than the 35B-A3B MoE, and ~3x slower than it in practice (242s vs 77s on the same bug-fix task). Scored a clean 15/15 on that task: minimal fix, every original test preserved, 3 new regression tests, changelog updated. Correctness is not the reason to prefer the MoE sibling; speed is. Validated 2026-08-24 on the 8-function module task: all three deliverables correct, 26 tests passing, a complete README, and it verified its own work by running `python -m pytest` correctly where gpt-oss:20b ran bare `pytest`, hit the Windows PATH gotcha, and reported success anyway. Needs ~25% CPU offload on a 16GB card; 666s on that task. Available via plain `ollama pull`, no manual server.",
    },
    "devstral:24b": {
        "approx_vram_gb": 14, "bfcl_path": None, "backend": "ollama",
        "active_params_b": 23.6,
        "measured_seconds_per_task": 26,
        "validated_at": "trivial", "documented_failure": True,
        "notes": "Dense 23.6B, Mistral's agentic coding model. Tool-calls fine on a trivial read-and-edit (38s, 2/2 real tool calls) after an earlier rejection here was found to be a harness-config artifact. But on the first REALISTIC task it was given (fix a bug in existing code, add regression tests, update a changelog) it did nothing at all: emitted one line of intent, called zero tools, left every file byte-identical, and exited 0. Scored 8/15, exactly the untouched baseline. The clearest case in this project that trivial-task validation predicts nothing about real work. Also the control in a runtime A/B: 2/2 tool calls via Ollama vs 0/2 via llama.cpp on the same GGUF, so for this model Ollama's chat template is the one that works.",
    },
    "qwen2.5-coder:14b": {
        "approx_vram_gb": 9, "active_params_b": 14.0, "bfcl_path": None, "backend": "ollama",
        "validated_at": None, "documented_failure": True,
        "notes": "REJECTED: no documented tool-calling support (confirmed via its own Hugging Face model card), 0/2 empirical failures through OpenCode.",
    },
    "qwen2.5:14b-instruct": {
        "approx_vram_gb": 9, "active_params_b": 14.0, "bfcl_path": None, "backend": "ollama",
        "validated_at": None, "documented_failure": True,
        "notes": "REJECTED: inconsistent through OpenCode (1 success, 1 hang with zero output on an identical run); raw API tool calls work fine, so the flakiness is specific to OpenCode's full prompt/schema load.",
    },
}


def get_vram_gb():
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
        capture_output=True, text=True,
    )
    return int(out.stdout.strip().split("\n")[0]) / 1024


def fetch_bfcl_score(bfcl_path):
    if not bfcl_path:
        return None
    url = f"{BFCL_BASE}/{bfcl_path}/multi_turn/BFCL_v4_multi_turn_base_score.json"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.readline())["accuracy"]
    except Exception:
        return None


def hf_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "foreman-recommend/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def hf_search(term, sort, limit):
    q = urllib.parse.quote(term)
    url = f"{HF_API}?search={q}&filter=gguf&sort={sort}&direction=-1&limit={limit}"
    try:
        return hf_get(url)
    except Exception:
        return []


def hf_best_fitting_quant(model_id, budget_gb):
    """Return (quant_name, size_gb) for the best quant that fits budget_gb,
    or None if nothing in the preference list fits. Uses the tree API for
    real file sizes, never guesses from parameter count."""
    try:
        tree = hf_get(f"{HF_API}/{urllib.parse.quote(model_id)}/tree/main")
    except Exception:
        return None
    files = {f["path"]: f.get("size", 0) for f in tree if f.get("path", "").endswith(".gguf")}
    for quant in QUANT_PREFERENCE:
        for path, size in files.items():
            if quant in path:
                size_gb = size / 1e9
                if size_gb <= budget_gb:
                    return path, size_gb
    return None


def _parse_ollama_library(html):
    """Yield (name, capabilities, size_labels) per model on Ollama's library
    page. Capabilities are Ollama's own metadata, so filtering on "tools" is
    the platform's claim about tool-calling, not a guess from the name."""
    for block in re.split(r"<li ", html)[1:]:
        m = re.search(r'href="/library/([a-z0-9._-]+)"', block)
        if not m:
            continue
        caps = re.findall(r"bg-indigo-50[^>]*>\s*([a-z]+)\s*<", block)
        sizes = re.findall(r"bg-\[#ddf4ff\][^>]*>\s*([0-9.]+[bmx]?)\s*<", block, re.I)
        yield m.group(1), caps, sizes


def ollama_tag_size_gb(model, candidate_tags):
    """Total download size for the first resolvable Ollama tag, from the
    registry manifest. Real layer sizes, not an estimate from parameter
    count."""
    for tag in candidate_tags:
        try:
            data = hf_get(f"{OLLAMA_REGISTRY}/{model}/manifests/{tag}")
        except Exception:
            continue
        layers = data.get("layers")
        if layers:
            return tag, sum(layer.get("size", 0) for layer in layers) / 1e9
    return None


def discover_ollama_candidates(budget_gb, limit):
    """Models in Ollama's own library that declare tool-calling support and
    fit the VRAM budget.

    This tier exists because the Hugging Face search below cannot see them:
    it filters on GGUF repos, and Ollama-native models (gpt-oss, qwen3.6,
    devstral) either aren't published as GGUF or don't match the search
    terms. Both models this project actually settled on were invisible to
    discovery until this was added, which is exactly the blind spot that
    made a hand-curated KNOWN list feel necessary.
    """
    try:
        html = urllib.request.urlopen(
            urllib.request.Request(OLLAMA_LIBRARY, headers={"User-Agent": "foreman-recommend/1.0"}),
            timeout=25,
        ).read().decode("utf-8", "replace")
    except Exception:
        return []

    results = []
    for name, caps, sizes in _parse_ollama_library(html):
        if "tools" not in caps:
            continue
        # Largest variant that still fits is the best use of the budget, so
        # try sizes big-to-small and keep only the first hit per family. One
        # row per model beats five rows of the same model at five sizes.
        parsed = []
        for size_label in sizes:
            try:
                params_b = float(re.sub(r"[^0-9.]", "", size_label) or 0)
            except ValueError:
                continue
            if params_b and params_b <= OLLAMA_MAX_PARAMS_B:
                parsed.append((params_b, [f"{size_label}{s}" for s in OLLAMA_TAG_SUFFIXES]))
        if not parsed:
            # No size badges: the family tags by quantization only. Params
            # are unknown, so 0 sorts it last among fitting candidates rather
            # than letting an unknown masquerade as small.
            parsed.append((0.0, OLLAMA_SIZELESS_TAGS))
        offload_ceiling = budget_gb * OFFLOAD_BUDGET_MULTIPLIER
        for params_b, candidate_tags in sorted(parsed, reverse=True):
            found = ollama_tag_size_gb(name, candidate_tags)
            if not found:
                continue
            tag, size_gb = found
            if size_gb > offload_ceiling:
                continue
            fits = size_gb <= budget_gb
            note = "DISCOVERED in Ollama's library, declares tool-calling support. NOT yet verified; Ollama's 'tools' tag is a capability claim, not evidence it works through a real harness (qwen2.5-coder claimed function calling and scored 0/2 here). Run stage 2 before trusting it."
            if not fits:
                note += f" Exceeds the VRAM budget by ~{size_gb - budget_gb:.1f}GB, so part of it runs on CPU: expect a speed penalty that gets worse as context grows, not a fixed one."
            results.append({
                "tag": f"{name}:{tag}",
                "params_b": params_b,
                "active_params_b": parse_active_params_b(f"{name}:{tag}"),
                "approx_vram_gb": round(size_gb, 1),
                "fits_without_offload": fits,
                "capabilities": caps,
                "backend": "ollama",
                "bfcl_multi_turn_accuracy": None,
                "measured_seconds_per_task": None,
                "notes": note,
            })
            break

    # Report the two categories separately rather than ranking them against
    # each other. Either single ordering fails: fitting-models-first fills the
    # list with small models and truncates the large ones away, while pure
    # parameter-count-first fills it with 32B models needing heavy offload and
    # truncates away the practical ones. Both failure modes were observed.
    # Splitting the budget guarantees each category is represented, and lets
    # the caller weigh "fits entirely" against "bigger but partly on CPU"
    # themselves, which is the actual trade-off and not one a sort can settle.
    #
    # Parameter count orders within each bucket. It's a crude capability proxy
    # and explicitly not a quality claim, but it beats Ollama's page order,
    # which is popularity and buries current models under 2024 ones.
    # Within "fits", bigger is the better use of the budget. Within "needs
    # offload", the opposite: rank by how little it overflows, because the
    # penalty scales with how much sits on the CPU. Sorting that bucket by
    # parameter count instead just surfaces 32B models that overflow hardest,
    # and ranks a 2024 35B above a current 27B on size alone.
    fitting = sorted((r for r in results if r["fits_without_offload"]), key=lambda r: -r["params_b"])
    offload = sorted((r for r in results if not r["fits_without_offload"]), key=lambda r: r["approx_vram_gb"])
    half = max(1, limit // 2)
    return fitting[:half] + offload[: limit - len(fitting[:half])]


def discover_candidates(budget_gb, search_terms, per_term_limit):
    """Live HF search, merged across terms and sort orders, deduped, filtered
    to models with a quant that actually fits budget_gb."""
    seen = {}
    for term in search_terms:
        for sort in ("downloads", "createdAt"):
            for m in hf_search(term, sort, per_term_limit):
                seen[m["id"]] = m

    results = []
    for model_id, meta in seen.items():
        fit = hf_best_fitting_quant(model_id, budget_gb)
        if not fit:
            continue
        quant, size_gb = fit
        results.append({
            "tag": model_id,
            "quant_file": quant,
            "approx_vram_gb": round(size_gb, 1),
            "downloads": meta.get("downloads", 0),
            "created": (meta.get("createdAt") or "")[:10],
            "backend": "llamacpp",
            "bfcl_multi_turn_accuracy": None,
            "measured_seconds_per_task": None,
            "notes": "DISCOVERED via Hugging Face search, fits VRAM budget, NOT yet verified. Do not trust until empirically tested (see SKILL.md stage 2).",
        })
    results.sort(key=lambda r: -r["downloads"])
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--vram-gb", type=float, help="Override detected VRAM (GB)")
    parser.add_argument("--headroom-gb", type=float, default=2.0,
                         help="VRAM headroom to reserve (default 2GB; this machine crashed once without enough)")
    parser.add_argument("--no-discover", action="store_true", help="Skip live Hugging Face discovery, known candidates only")
    parser.add_argument("--search", action="append", help="Add a custom discovery search term (repeatable)")
    parser.add_argument("--discover-limit", type=int, default=8, help="Max results per search term/sort combination")
    args = parser.parse_args()

    vram = args.vram_gb if args.vram_gb else get_vram_gb()
    budget = vram - args.headroom_gb

    known = []
    for tag, info in KNOWN_CANDIDATES.items():
        if info["notes"].startswith("REJECTED"):
            continue
        score = fetch_bfcl_score(info["bfcl_path"])
        known.append({
            "tag": tag,
            "fits_without_offload": info["approx_vram_gb"] <= budget,
            "approx_vram_gb": info["approx_vram_gb"],
            "bfcl_multi_turn_accuracy": score,
            "measured_seconds_per_task": info.get("measured_seconds_per_task"),
            "backend": info.get("backend", "ollama"),
            "validated_at": info.get("validated_at"),
            "documented_failure": info.get("documented_failure", False),
            "active_params_b": info.get("active_params_b"),
            "notes": info["notes"],
        })

    def sort_key(r):
        # Reliability first, speed second. A model that silently produces
        # broken output isn't made acceptable by producing it quickly.
        timed = r["measured_seconds_per_task"] is not None
        return (
            -VALIDATION_RANK.get(r["validated_at"], 0),
            r["documented_failure"],
            not timed,
            r["measured_seconds_per_task"] if timed else 0,
            not r["fits_without_offload"],
            -(r["bfcl_multi_turn_accuracy"] or -1),
        )
    known.sort(key=sort_key)

    print(f"Detected VRAM: {vram:.1f}GB  (budget after {args.headroom_gb:.1f}GB headroom: {budget:.1f}GB)\n")
    print("=== KNOWN (hand-curated, BFCL-scored and/or empirically tested) ===\n")
    validation_label = {
        "moderate": "verified on a real multi-function task",
        "moderate-partial": "multi-function task, one requirement silently shorted",
        "trivial": "TRIVIAL TASKS ONLY",
        None: "not empirically tested",
    }
    for r in known:
        score_str = f"{r['bfcl_multi_turn_accuracy'] * 100:.1f}%" if r["bfcl_multi_turn_accuracy"] is not None else "not in BFCL"
        fit_str = "fits, no offload" if r["fits_without_offload"] else "needs CPU offload"
        speed_str = f"{r['measured_seconds_per_task']}s/task (measured)" if r["measured_seconds_per_task"] is not None else "not yet measured"
        verified_str = validation_label[r["validated_at"]]
        if r["documented_failure"]:
            verified_str += "  [HAS A DOCUMENTED FAILURE]"
        print(f"- {r['tag']}  [{r['backend']}]")
        print(f"    Verified: {verified_str}")
        active = r.get("active_params_b")
        arch_str = "active params unknown"
        if active is not None:
            arch_str = f"~{active}B active/token"
        print(f"    Speed: {speed_str} ({arch_str})   BFCL multi-turn: {score_str}   VRAM: ~{r['approx_vram_gb']}GB ({fit_str})")
        print(f"    {r['notes']}")
        print()

    if known:
        fitting_known = [r for r in known if r["fits_without_offload"] or r["measured_seconds_per_task"] is not None]
        if fitting_known:
            top = fitting_known[0]
            print(f"Top known candidate: {top['tag']}")
            print(f"  (ranked by how far it's actually been verified, then by speed;")
            print(f"   a faster model with a worse verification record sorts below it)\n")

    if not args.no_discover:
        print("=== DISCOVERED (Ollama library, declares tool-calling support) ===\n")
        try:
            ollama_found = discover_ollama_candidates(budget, args.discover_limit)
        except urllib.error.URLError as e:
            ollama_found = []
            print(f"(Ollama library discovery failed, network error: {e})\n")
        if not ollama_found:
            print("(no fitting tool-calling models found in Ollama's library)\n")
        for r in ollama_found:
            fit_str = "fits, no offload" if r["fits_without_offload"] else "NEEDS CPU OFFLOAD"
            print(f"- {r['tag']}  [{r['backend']}]")
            active = r.get("active_params_b")
            arch_str = f"MoE, ~{active}B active/token" if active else "active params unknown"
            print(f"    VRAM: ~{r['approx_vram_gb']}GB ({fit_str})   {arch_str}   capabilities: {', '.join(r['capabilities'])}")
            print(f"    {r['notes']}")
            print()

        terms = args.search if args.search else DISCOVERY_SEARCH_TERMS
        print(f"=== DISCOVERED (live Hugging Face search: {', '.join(terms)}) ===\n")
        try:
            discovered = discover_candidates(budget, terms, args.discover_limit)
        except urllib.error.URLError as e:
            discovered = []
            print(f"(discovery failed, network error: {e})\n")
        if not discovered:
            print("(no new fitting candidates found this run)\n")
        for r in discovered[:10]:
            print(f"- {r['tag']}  [{r['backend']}]")
            print(f"    Quant: {r['quant_file']}   VRAM: ~{r['approx_vram_gb']}GB   downloads: {r['downloads']:,}   created: {r['created']}")
            print(f"    {r['notes']}")
            print()

    print("Nothing above is a final answer. KNOWN entries with measured speed are")
    print("trustworthy for their tested harness; DISCOVERED entries are untested by")
    print("definition. Always run stage 2 (3x tool-calling test, AGENTS.md fix,")
    print("independently verify the file changed) before recommending a model for")
    print("real use, see SKILL.md.")


if __name__ == "__main__":
    main()
