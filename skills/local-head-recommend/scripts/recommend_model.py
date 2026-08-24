#!/usr/bin/env python3
"""Shortlist local models for agentic tool use, given this machine's hardware.

Two-stage design (this script is stage 1):
  1. Shortlist candidates by BFCL (Berkeley Function Calling Leaderboard)
     multi-turn tool-calling accuracy and VRAM fit — no guessing, no trusting
     marketing claims or blog posts.
  2. Hand the top 2-3 candidates to empirical verification (pull, run the
     standard tool-calling test with the AGENTS.md fix, independently verify
     file changes) before trusting the shortlist — BFCL scores the model's
     trained capability, not whether a specific quant actually works through
     Ollama's/llama.cpp's chat-template parsing. That gap has broken real
     candidates before (qwen2.5-coder) despite fine benchmark claims.

The candidate list below is curated, not auto-discovered — BFCL's model
naming doesn't map cleanly to Ollama tags, so entries are added by hand as
they're found and verified. Extend CANDIDATES as new models are evaluated.

Usage:
    python recommend_model.py [--vram-gb N] [--headroom-gb N]
"""
import argparse
import json
import subprocess
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

BFCL_BASE = "https://raw.githubusercontent.com/HuanzhiMao/BFCL-Result/main/2025-12-16/score"

# tag -> approx VRAM at Q4-class quantization (GB), BFCL result-dir name (or
# None if not covered), and notes carrying forward what's already known.
CANDIDATES = {
    "qwen3:8b": {
        "approx_vram_gb": 8, "bfcl_path": "Qwen_Qwen3-8B-FC",
        "notes": "Dense. Validated 2026-08-23: 3/3 clean tool-calling through OpenCode, 28-39s/task — fastest and most reliable candidate found so far.",
    },
    "qwen3:14b": {
        "approx_vram_gb": 10, "bfcl_path": "qwen3-14b-FC",
        "notes": "Dense. Lower BFCL multi-turn score than qwen3:8b despite being larger — not yet empirically tested.",
    },
    "qwen3:32b": {
        "approx_vram_gb": 20, "bfcl_path": "Qwen_Qwen3-32B-FC",
        "notes": "Dense. Highest BFCL score of the Qwen3 family checked, but needs CPU offload on a 16GB card — not yet empirically tested; offload previously correlated with severe slowdowns under OpenCode.",
    },
    "qwen3-coder:30b": {
        "approx_vram_gb": 19, "bfcl_path": "qwen3-30b-a3b-instruct-2507-FC",
        "notes": "MoE ~3.3B active params. BFCL path is the closest available match (base instruct, not the coder fine-tune) — approximate. Validated reliable (3/3) but needs CPU offload on 16GB; times out through OpenCode's heavy prompt (worked in the repo's own lean ollama-agent-head harness, and via llama.cpp with --fit).",
    },
    "gpt-oss:20b": {
        "approx_vram_gb": 13, "bfcl_path": None,
        "notes": "Not in BFCL's coverage. Empirically validated 3/3 through OpenCode (with AGENTS.md fix), fits fully in 16GB VRAM, ~110s/task.",
    },
    "qwen2.5-coder:14b": {
        "approx_vram_gb": 9, "bfcl_path": None,
        "notes": "REJECTED — no documented tool-calling support (confirmed via its own Hugging Face model card), 0/2 empirical failures through OpenCode.",
    },
    "qwen2.5:14b-instruct": {
        "approx_vram_gb": 9, "bfcl_path": None,
        "notes": "REJECTED — inconsistent through OpenCode (1 success, 1 hang with zero output on an identical run); raw API tool calls work fine, so the flakiness is specific to OpenCode's full prompt/schema load.",
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--vram-gb", type=float, help="Override detected VRAM (GB)")
    parser.add_argument("--headroom-gb", type=float, default=2.0,
                         help="VRAM headroom to reserve (default 2GB — this machine crashed once without enough)")
    args = parser.parse_args()

    vram = args.vram_gb if args.vram_gb else get_vram_gb()
    budget = vram - args.headroom_gb

    results = []
    for tag, info in CANDIDATES.items():
        if info["notes"].startswith("REJECTED"):
            continue
        fits = info["approx_vram_gb"] <= budget
        score = fetch_bfcl_score(info["bfcl_path"])
        results.append({
            "tag": tag,
            "fits_without_offload": fits,
            "approx_vram_gb": info["approx_vram_gb"],
            "bfcl_multi_turn_accuracy": score,
            "notes": info["notes"],
        })

    # Prefer models that fit without CPU offload, then by BFCL score (unscored last).
    results.sort(key=lambda r: (not r["fits_without_offload"], -(r["bfcl_multi_turn_accuracy"] or -1)))

    print(f"Detected VRAM: {vram:.1f}GB  (budget after {args.headroom_gb:.1f}GB headroom: {budget:.1f}GB)\n")
    for r in results:
        score_str = f"{r['bfcl_multi_turn_accuracy'] * 100:.1f}%" if r["bfcl_multi_turn_accuracy"] is not None else "not in BFCL"
        fit_str = "fits, no offload" if r["fits_without_offload"] else "needs CPU offload"
        print(f"- {r['tag']}")
        print(f"    BFCL multi-turn: {score_str}   VRAM: ~{r['approx_vram_gb']}GB ({fit_str})")
        print(f"    {r['notes']}")
        print()

    if results:
        print(f"Top candidate: {results[0]['tag']}")
    print("This is a shortlist, not a final answer — verify empirically before trusting it")
    print("(pull it, run the standard tool-calling test with the AGENTS.md fix, check the")
    print("file actually changed rather than trusting the model's own 'done' message).")


if __name__ == "__main__":
    main()
