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
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

BFCL_BASE = "https://raw.githubusercontent.com/HuanzhiMao/BFCL-Result/main/2025-12-16/score"
HF_API = "https://huggingface.co/api/models"

# Reasonable default quant preference order, balancing quality against size;
# the first one found in a repo's file list that fits the VRAM budget is used.
QUANT_PREFERENCE = ["Q4_K_M", "Q4_K_S", "IQ4_XS", "Q4_0", "Q5_K_M", "Q3_K_M"]

# Search terms used for HF discovery. Biased toward instruction-tuned and
# coding-oriented models, since that's this skill's actual use case. A
# generic "GGUF" search surfaces plenty of chat/roleplay models that aren't
# relevant here. Extend/adjust as the space shifts.
DISCOVERY_SEARCH_TERMS = ["instruct GGUF", "coder GGUF", "agent GGUF"]

# tag -> approx VRAM at Q4-class quantization (GB), BFCL result-dir name (or
# None if not covered), acquisition backend, and notes carrying forward what's
# already known. backend "ollama" means `ollama pull <tag>`; "llamacpp" means
# a manual GGUF download run through llama.cpp's own server (needed for
# models not published to Ollama's library, or that need --fit-style manual
# CPU/GPU tuning Ollama doesn't expose).
KNOWN_CANDIDATES = {
    "qwen3:8b": {
        "approx_vram_gb": 8, "bfcl_path": "Qwen_Qwen3-8B-FC", "backend": "ollama",
        "measured_seconds_per_task": 33,
        "notes": "Dense. Validated 2026-08-23: 3/3 clean tool-calling through OpenCode, 28-39s/task, fastest and most reliable candidate found so far.",
    },
    "qwen3:14b": {
        "approx_vram_gb": 10, "bfcl_path": "qwen3-14b-FC", "backend": "ollama",
        "notes": "Dense. Lower BFCL multi-turn score than qwen3:8b despite being larger; not yet empirically tested.",
    },
    "qwen3:32b": {
        "approx_vram_gb": 20, "bfcl_path": "Qwen_Qwen3-32B-FC", "backend": "ollama",
        "notes": "Dense. Highest BFCL score of the Qwen3 family checked, but needs CPU offload on a 16GB card. Not yet empirically tested; offload previously correlated with severe slowdowns under OpenCode.",
    },
    "qwen3-coder:30b": {
        "approx_vram_gb": 19, "bfcl_path": "qwen3-30b-a3b-instruct-2507-FC", "backend": "ollama",
        "measured_seconds_per_task": None,  # timed out (>180s) via OpenCode+Ollama; 190s via OpenCode+llama.cpp+--fit
        "notes": "MoE ~3.3B active params. BFCL path is the closest available match (base instruct, not the coder fine-tune), approximate. Validated reliable (3/3) but needs CPU offload on 16GB; times out through OpenCode's heavy prompt over plain Ollama, but works via llama.cpp with --fit (see docs/opencode-setup.md).",
    },
    "gpt-oss:20b": {
        "approx_vram_gb": 13, "bfcl_path": None, "backend": "ollama",
        "measured_seconds_per_task": 110,
        "notes": "Not in BFCL's coverage. Empirically validated 3/3 through OpenCode (with AGENTS.md fix), fits fully in 16GB VRAM, ~110s/task.",
    },
    "Qwen3.6-35B-A3B-UD-Q4_K_M": {
        "approx_vram_gb": 15, "bfcl_path": None, "backend": "llamacpp",
        "measured_seconds_per_task": 49,
        "notes": "MoE, hybrid attention/recurrent architecture (unsloth GGUF). Not on Ollama's library and not in BFCL (too new as of the Dec 2025 snapshot); found via a community post, not this shortlister, which is part of why HF-based discovery was added. Empirically validated 2026-08-23: 3/3 through OpenCode+llama.cpp (--fit on --fit-ctx 65536 --fit-target 256 -np 1 -fa on --no-mmap --mlock -b 2048 -ub 2048 -ctk/-ctv q8_0), 43-55s/task. Note --mlock holds ~19GB in physical RAM for as long as the server runs; must be stopped manually when done, unlike Ollama it does not auto-unload on idle.",
    },
    "qwen2.5-coder:14b": {
        "approx_vram_gb": 9, "bfcl_path": None, "backend": "ollama",
        "notes": "REJECTED: no documented tool-calling support (confirmed via its own Hugging Face model card), 0/2 empirical failures through OpenCode.",
    },
    "qwen2.5:14b-instruct": {
        "approx_vram_gb": 9, "bfcl_path": None, "backend": "ollama",
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
            "notes": info["notes"],
        })

    def sort_key(r):
        timed = r["measured_seconds_per_task"] is not None
        return (
            not timed,
            r["measured_seconds_per_task"] if timed else 0,
            not r["fits_without_offload"],
            -(r["bfcl_multi_turn_accuracy"] or -1),
        )
    known.sort(key=sort_key)

    print(f"Detected VRAM: {vram:.1f}GB  (budget after {args.headroom_gb:.1f}GB headroom: {budget:.1f}GB)\n")
    print("=== KNOWN (hand-curated, BFCL-scored and/or empirically tested) ===\n")
    for r in known:
        score_str = f"{r['bfcl_multi_turn_accuracy'] * 100:.1f}%" if r["bfcl_multi_turn_accuracy"] is not None else "not in BFCL"
        fit_str = "fits, no offload" if r["fits_without_offload"] else "needs CPU offload"
        speed_str = f"{r['measured_seconds_per_task']}s/task (measured)" if r["measured_seconds_per_task"] is not None else "not yet measured"
        print(f"- {r['tag']}  [{r['backend']}]")
        print(f"    Speed: {speed_str}   BFCL multi-turn: {score_str}   VRAM: ~{r['approx_vram_gb']}GB ({fit_str})")
        print(f"    {r['notes']}")
        print()

    if known:
        fitting_known = [r for r in known if r["fits_without_offload"] or r["measured_seconds_per_task"] is not None]
        if fitting_known:
            print(f"Top known candidate: {fitting_known[0]['tag']}\n")

    if not args.no_discover:
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
